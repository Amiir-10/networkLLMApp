import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import TopologyPane from "./components/TopologyPane";
import ChatPane from "./components/ChatPane";
import {
  fetchHealth,
  fetchRules,
  startLab,
  stopLab,
  type HealthResponse,
  type ParsedRule,
  type ToolCallResult,
  type PingTestResult,
} from "./api";

const SCENARIO = "central-hub";
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
  const [model, setModel] = useState(MODELS[0]);
  const [labLoading, setLabLoading] = useState(false);
  const [labError, setLabError] = useState<string | null>(null);
  const [firewallRules, setFirewallRules] = useState<ParsedRule[]>([]);
  const [pingEvent, setPingEvent] = useState<PingEvent | null>(null);
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
      await startLab(SCENARIO);
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
      await stopLab(SCENARIO);
      await pollHealth();
      setFirewallRules([]);
      setPingEvent(null);
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
            <ChatPane model={model} labActive={labReady} onChatComplete={handleChatComplete} />
          </div>
        </div>
      </div>

      {/* Bottom bar */}
      <div className="border-t border-gray-200 px-4 py-2 flex items-center gap-4 bg-gray-50">
        <div className="flex gap-2">
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
          {labActive && <span className="text-gray-400">Scenario: {SCENARIO}</span>}
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
