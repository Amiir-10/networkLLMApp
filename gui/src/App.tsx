import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import ChatView from "./components/ChatView";
import ConsoleView from "./components/ConsoleView";
import BrowserView, { initialBrowserState, type BrowserTabState } from "./components/BrowserView";
import {
  fetchHealth,
  fetchModels,
  fetchRules,
  fetchVulnerable,
  fetchScenarios,
  fetchScenario,
  startLab,
  stopLab,
  resetLab,
  sendChat,
  clearChat,
  type HealthResponse,
  type ParsedRule,
  type ToolCallResult,
  type PingTestResult,
  type PingEvent,
  type ScenarioSummary,
  type ChatMessage,
} from "./api";
import { buildTopology, type BuiltTopology } from "./topology";
import bearingpointLogo from "./assets/bearingpoint.png";

// Shown until the backend answers /models with what Ollama actually serves
// (local service or GPU tunnel) — then replaced by the real list.
const FALLBACK_MODELS = ["llama3.1:8b", "qwen2.5-coder:7b"];
const RULE_MUTATING_TOOLS = new Set(["block_traffic", "allow_traffic", "flush_rules"]);
const VULN_TOOLS = new Set(["vulnerability_scan", "run_command"]);

type View = "chat" | "console" | "browser";

function isPingBlocked(lossLine: string | undefined): boolean {
  if (!lossLine) return true;
  return !/\b0% packet loss\b/.test(lossLine);
}

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenario, setScenario] = useState("central-hub");
  const [topology, setTopology] = useState<BuiltTopology | null>(null);
  const [models, setModels] = useState<string[]>(FALLBACK_MODELS);
  const [model, setModel] = useState(FALLBACK_MODELS[0]);
  const [labLoading, setLabLoading] = useState(false);
  const [labError, setLabError] = useState<string | null>(null);
  const [firewallRules, setFirewallRules] = useState<ParsedRule[]>([]);
  // Node ids flagged vulnerable by a scan (point 12): the topology paints these
  // red with a "(vulnerable)" label until an LLM fix (run_command) clears them.
  const [vulnerableNodes, setVulnerableNodes] = useState<string[]>([]);
  const [pingEvent, setPingEvent] = useState<PingEvent | null>(null);
  // Chat conversation lives here, not in ChatPane, so it survives switching the
  // chat<->console tabs within a session (ChatPane unmounts on tab change; App
  // does not). The backend keeps its own _conversation_history; this is the UI
  // mirror. Both are cleared together on lab start/stop/reset and Clear.
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  // Browser tab state lives here (like the chat) so the last-visited page
  // survives the browse -> block-via-chat -> browse-again demo flow.
  const [browserState, setBrowserState] = useState<BrowserTabState>(initialBrowserState);
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

  useEffect(() => {
    fetchScenarios().then(setScenarios).catch(() => setScenarios([]));
  }, []);

  useEffect(() => {
    fetchModels()
      .then(({ models: names, defaultModel }) => {
        if (names.length === 0) return; // Ollama up but empty — keep fallback
        setModels(names);
        // Showcase hosting advertises the provisioned+warm model — land on it.
        // Runs once at mount, so this can never override a user's choice.
        setModel((m) => defaultModel ?? (names.includes(m) ? m : names[0]));
      })
      .catch(() => {}); // backend or Ollama unreachable — keep fallback
  }, []);

  // Build the topology for the selected scenario (reads YAML; no lab needed),
  // so both the chat and console views render a preview before any lab starts.
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
      setFirewallRules((await fetchRules()).parsed);
    } catch {
      /* best-effort; keep previous */
    }
  }, [labReady]);

  const refetchVulnerable = useCallback(async () => {
    if (!labReady) {
      setVulnerableNodes([]);
      return;
    }
    try {
      setVulnerableNodes(await fetchVulnerable());
    } catch {
      /* best-effort; keep previous */
    }
  }, [labReady]);

  useEffect(() => {
    refetchRules();
    refetchVulnerable();
  }, [refetchRules, refetchVulnerable]);

  const dropRules = useMemo(() => firewallRules.filter((r) => r.action === "drop"), [firewallRules]);

  async function handleStartLab() {
    setLabLoading(true);
    setLabError(null);
    try {
      await startLab(scenario);
      await pollHealth();
      setChatMessages([]); // backend clears _conversation_history on start
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
      setChatMessages([]); // backend clears _conversation_history on stop
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  async function handleResetLab() {
    if (!window.confirm("Reset the lab? This destroys and redeploys every container and clears the chat. Takes ~30–60s.")) return;
    setLabLoading(true);
    setLabError(null);
    try {
      await resetLab(scenario);
      await pollHealth();
      setFirewallRules([]);
      setPingEvent(null);
      setChatMessages([]); // backend clears _conversation_history on reset
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  const handleChatComplete = useCallback(
    (toolCalls: ToolCallResult[]) => {
      if (toolCalls.some((tc) => RULE_MUTATING_TOOLS.has(tc.tool))) refetchRules();
      // A scan flags vulnerable nodes; run_command may fix one — either changes
      // which nodes are red, so refresh the vulnerable set after those tools.
      if (toolCalls.some((tc) => VULN_TOOLS.has(tc.tool))) refetchVulnerable();
      const ping = [...toolCalls].reverse().find((tc) => tc.tool === "ping_test");
      if (ping && ping.args && ping.result && !ping.error) {
        const args = ping.args as { src?: string; dst?: string };
        const result = ping.result as PingTestResult;
        if (args.src && args.dst) {
          pingIdRef.current += 1;
          setPingEvent({ id: pingIdRef.current, src: args.src, dst: args.dst, blocked: isPingBlocked(result.loss_line) });
        }
      }
    },
    [refetchRules, refetchVulnerable]
  );

  const handlePingEventComplete = useCallback(() => setPingEvent(null), []);

  // Send lives here (not in ChatPane) so an in-flight ~50s request still lands
  // its response if the user switches to the console tab and back while waiting.
  const sendChatMessage = useCallback(
    async (text: string) => {
      setChatMessages((prev) => [...prev, { role: "user", content: text }]);
      setChatLoading(true);
      try {
        const resp = await sendChat(text, model);
        handleChatComplete(resp.tool_calls);
        setChatMessages((prev) => [
          ...prev,
          { role: "assistant", content: resp.response || "", toolCalls: resp.tool_calls, metrics: resp.metrics },
        ]);
      } catch (err) {
        setChatMessages((prev) => [...prev, { role: "assistant", content: "", error: String(err) }]);
      } finally {
        setChatLoading(false);
      }
    },
    [model, handleChatComplete]
  );

  const clearChatHistory = useCallback(async () => {
    try {
      await clearChat();
    } catch {
      /* best-effort */
    }
    setChatMessages([]);
  }, []);

  const tabClass = (active: boolean) =>
    `px-3 py-1 text-xs font-medium rounded transition-colors ${
      active ? "bg-white text-gray-800 shadow-sm border border-gray-200" : "text-gray-500 hover:text-gray-700"
    }`;

  return (
    // pb keeps the console/fit-button off the very bottom edge of the screen —
    // the supervisor's laptop had them flush against it (point 9). Percentage
    // (vh) so it scales with the display.
    <div className="h-screen flex flex-col bg-white pb-[1.2vh]">
      {/* Unified top bar: app title · view tabs · scenario + lab controls · status */}
      <header className="border-b border-gray-200 px-4 py-2 flex items-center gap-4 bg-gray-50">
        <span className="flex items-center gap-2">
          <img src={bearingpointLogo} alt="BearingPoint" className="h-6 w-auto" />
          <span className="text-sm font-bold text-gray-800">NetSec Zero-Trust Co-pilot</span>
        </span>

        <nav className="flex items-center gap-1 bg-gray-100 rounded-md p-0.5">
          <button className={tabClass(view === "chat")} onClick={() => setView("chat")}>Chat</button>
          <button className={tabClass(view === "console")} onClick={() => setView("console")}>Console</button>
          <button className={tabClass(view === "browser")} onClick={() => setView("browser")}>Browser</button>
        </nav>

        <div className="ml-auto flex items-center gap-3">
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

          <div className="flex gap-2">
            <button onClick={handleStartLab} disabled={labLoading || labActive} className="bg-green-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-green-700 transition-colors">
              {labLoading && !labActive ? "Starting..." : "Start"}
            </button>
            <button onClick={handleStopLab} disabled={labLoading || !labActive} className="bg-red-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-red-700 transition-colors">
              {labLoading && labActive ? "Stopping..." : "Stop"}
            </button>
            <button onClick={handleResetLab} disabled={labLoading || !labActive} title="Destroy + redeploy all containers and clear the chat" className="bg-amber-600 text-white px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40 hover:bg-amber-700 transition-colors">
              {labLoading && labActive ? "Working..." : "Reset"}
            </button>
          </div>

          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${backendUp ? "bg-green-500" : "bg-red-500"}`} />Backend</span>
            <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${labActive ? "bg-green-500" : "bg-gray-300"}`} />Lab</span>
            <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${fwConnected ? "bg-green-500" : "bg-gray-300"}`} />Firewall</span>
            {labReady && dropRules.length > 0 && (
              <span className="text-gray-400">{dropRules.length} DROP{dropRules.length === 1 ? "" : "s"}</span>
            )}
          </div>

          {labError && <span className="text-xs text-red-500 max-w-[12rem] truncate" title={labError}>{labError}</span>}
        </div>
      </header>

      {/* All three views stay MOUNTED and are toggled with CSS (hidden), so
          switching tabs never destroys state — in particular the Console
          view's open PTY shells keep their scrollback and live session
          (supervisor request 2026-08-25, point 2). */}
      <div className={view === "chat" ? "flex-1 flex flex-col min-h-0" : "hidden"}>
        <ChatView
          topology={topology}
          labReady={labReady}
          dropRules={dropRules}
          vulnerableNodes={vulnerableNodes}
          pingEvent={pingEvent}
          onPingEventComplete={handlePingEventComplete}
          model={model}
          setModel={setModel}
          models={models}
          messages={chatMessages}
          chatLoading={chatLoading}
          onSend={sendChatMessage}
          onClear={clearChatHistory}
        />
      </div>
      <div className={view === "console" ? "flex-1 flex flex-col min-h-0" : "hidden"}>
        <ConsoleView
          topology={topology}
          labReady={labReady}
          scenario={scenario}
          dropRules={dropRules}
          vulnerableNodes={vulnerableNodes}
          refetchRules={refetchRules}
        />
      </div>
      <div className={view === "browser" ? "flex-1 flex flex-col min-h-0" : "hidden"}>
        <BrowserView
          topology={topology}
          labReady={labReady}
          state={browserState}
          setState={setBrowserState}
        />
      </div>
    </div>
  );
}
