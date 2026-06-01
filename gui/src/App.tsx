import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import TopologyPane from "./components/TopologyPane";
import ChatPane from "./components/ChatPane";
import {
  fetchHealth,
  fetchRules,
  fetchScenarios,
  fetchScenario,
  startLab,
  stopLab,
  resetLab,
  type HealthResponse,
  type ParsedRule,
  type ToolCallResult,
  type PingTestResult,
  type ScenarioSummary,
} from "./api";
import { buildTopology, type BuiltTopology } from "./topology";

const MODELS = ["llama3.1:8b", "qwen2.5-coder:7b"];
const RULE_MUTATING_TOOLS = new Set(["block_traffic", "allow_traffic", "flush_rules"]);

export interface PingEvent {
  id: number;
  src: string;
  dst: string;
  blocked: boolean;
}

function isPingBlocked(lossLine: string | undefined): boolean {
  if (!lossLine) return true;
  // Examples:
  //   "2 packets transmitted, 2 received, 0% packet loss, time 1003ms"  (pass)
  //   "2 packets transmitted, 0 received, 100% packet loss, time 1004ms" (blocked)
  return !/\b0% packet loss\b/.test(lossLine);
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenario, setScenario] = useState("central-hub");
  const [topology, setTopology] = useState<BuiltTopology | null>(null);
  const [model, setModel] = useState(MODELS[0]);
  const [labLoading, setLabLoading] = useState(false);
  const [labError, setLabError] = useState<string | null>(null);
  const [firewallRules, setFirewallRules] = useState<ParsedRule[]>([]);
  const [pingEvent, setPingEvent] = useState<PingEvent | null>(null);
  // Bumped on reset to force-remount ChatPane, clearing the visible conversation
  // (backend history is cleared server-side by /lab/reset).
  const [chatResetKey, setChatResetKey] = useState(0);
  const pingIdRef = useRef(0);

  const pollHealth = useCallback(async () => {
    try {
      setHealth(await fetchHealth());
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    pollHealth();
    const id = setInterval(pollHealth, 5000);
    return () => clearInterval(id);
  }, [pollHealth]);

  // Available scenarios for the dropdown.
  useEffect(() => {
    fetchScenarios().then(setScenarios).catch(() => setScenarios([]));
  }, []);

  // Build the topology for the selected scenario (reads the YAML; no lab needed).
  useEffect(() => {
    let cancelled = false;
    fetchScenario(scenario)
      .then((g) => { if (!cancelled) setTopology(buildTopology(g)); })
      .catch(() => { if (!cancelled) setTopology(null); });
    return () => { cancelled = true; };
  }, [scenario]);

  // If a lab is already running (e.g. after a reload), follow its scenario.
  useEffect(() => {
    if (health?.lab_active && health.scenario && health.scenario !== scenario) {
      setScenario(health.scenario);
    }
  }, [health?.lab_active, health?.scenario, scenario]);

  const labActive = health?.lab_active ?? false;
  const fwConnected = health?.firewall_connected ?? false;
  const backendUp = health !== null;
  const labReady = labActive && fwConnected;

  const refetchRules = useCallback(async () => {
    if (!labReady) {
      setFirewallRules([]);
      return;
    }
    try {
      const r = await fetchRules();
      setFirewallRules(r.parsed);
    } catch {
      // best-effort; leave previous rules in place
    }
  }, [labReady]);

  // Refetch when lab readiness changes.
  useEffect(() => {
    refetchRules();
  }, [refetchRules]);

  const dropRules = useMemo(
    () => firewallRules.filter((r) => r.action === "drop"),
    [firewallRules]
  );

  async function handleStartLab() {
    setLabLoading(true);
    setLabError(null);
    try {
      await startLab(scenario);
      await pollHealth();
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  async function handleStopLab() {
    setLabLoading(true);
    setLabError(null);
    try {
      await stopLab(scenario);
      await pollHealth();
      setFirewallRules([]);
      setPingEvent(null);
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  async function handleResetLab() {
    if (
      !window.confirm(
        "Reset the lab? This destroys and redeploys every container and clears the chat. Takes ~30–60s."
      )
    ) {
      return;
    }
    setLabLoading(true);
    setLabError(null);
    try {
      await resetLab(scenario);
      await pollHealth();
      setFirewallRules([]);
      setPingEvent(null);
      setChatResetKey((k) => k + 1);
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  const handleChatComplete = useCallback(
    (toolCalls: ToolCallResult[]) => {
      if (toolCalls.some((tc) => RULE_MUTATING_TOOLS.has(tc.tool))) {
        refetchRules();
      }
      // Pick the last ping_test (LLM may emit multiple per response — animate the most recent).
      const ping = [...toolCalls].reverse().find((tc) => tc.tool === "ping_test");
      if (ping && ping.args && ping.result && !ping.error) {
        const args = ping.args as { src?: string; dst?: string };
        const result = ping.result as PingTestResult;
        if (args.src && args.dst) {
          pingIdRef.current += 1;
          setPingEvent({
            id: pingIdRef.current,
            src: args.src,
            dst: args.dst,
            blocked: isPingBlocked(result.loss_line),
          });
        }
      }
    },
    [refetchRules]
  );

  const handlePingEventComplete = useCallback(() => {
    setPingEvent(null);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-white">
      <div className="flex-1 flex min-h-0">
        {/* Topology pane */}
        <div className="w-1/2 border-r border-gray-200">
          <div className="px-4 py-3 border-b border-gray-200">
            <h2 className="text-sm font-semibold text-gray-700">Network Topology</h2>
          </div>
          <div className="h-[calc(100%-49px)]">
            <TopologyPane
              topology={topology}
              labReady={labReady}
              dropRules={dropRules}
              pingEvent={pingEvent}
              onPingEventComplete={handlePingEventComplete}
            />
          </div>
        </div>

        {/* Chat pane */}
        <div className="w-1/2">
          <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">Chat</h2>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="text-xs border border-gray-300 rounded px-2 py-1 bg-white"
            >
              {MODELS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div className="h-[calc(100%-49px)]">
            <ChatPane key={chatResetKey} model={model} labActive={labReady} onChatComplete={handleChatComplete} />
          </div>
        </div>
      </div>

      {/* Bottom bar */}
      <div className="border-t border-gray-200 px-4 py-2 flex items-center gap-4 bg-gray-50">
        <div className="flex items-center gap-2">
          <select
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
            disabled={labActive || labLoading}
            title={labActive ? "Stop the lab to switch scenarios" : "Choose a scenario"}
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white disabled:opacity-50"
          >
            {scenarios.map((s) => (
              <option key={s.name} value={s.name}>{s.name}</option>
            ))}
          </select>
          <button
            onClick={handleStartLab}
            disabled={labLoading || labActive}
            className="bg-green-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-green-700 transition-colors"
          >
            {labLoading && !labActive ? "Starting..." : "Start Lab"}
          </button>
          <button
            onClick={handleStopLab}
            disabled={labLoading || !labActive}
            className="bg-red-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-red-700 transition-colors"
          >
            {labLoading && labActive ? "Stopping..." : "Stop Lab"}
          </button>
          <button
            onClick={handleResetLab}
            disabled={labLoading || !labActive}
            title="Destroy + redeploy all containers and clear the chat"
            className="bg-amber-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-amber-700 transition-colors"
          >
            {labLoading && labActive ? "Working..." : "Reset"}
          </button>
        </div>

        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className={`w-2 h-2 rounded-full ${backendUp ? "bg-green-500" : "bg-red-500"}`} />
            Backend
          </span>
          <span className="flex items-center gap-1">
            <span className={`w-2 h-2 rounded-full ${labActive ? "bg-green-500" : "bg-gray-300"}`} />
            Lab
          </span>
          <span className="flex items-center gap-1">
            <span className={`w-2 h-2 rounded-full ${fwConnected ? "bg-green-500" : "bg-gray-300"}`} />
            Firewall
          </span>
          {labActive && <span className="text-gray-400">Scenario: {health?.scenario ?? scenario}</span>}
          {labReady && dropRules.length > 0 && (
            <span className="text-gray-400">
              · {dropRules.length} DROP rule{dropRules.length === 1 ? "" : "s"}
            </span>
          )}
        </div>

        {labError && (
          <span className="text-xs text-red-500 ml-auto">{labError}</span>
        )}
      </div>
    </div>
  );
}
