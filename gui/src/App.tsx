import { useState, useEffect, useCallback } from "react";
import TopologyPane, { type ConnectionStatus } from "./components/TopologyPane";
import ChatPane from "./components/ChatPane";
import { fetchHealth, startLab, stopLab, type HealthResponse } from "./api";

const SCENARIO = "small-soc";
const MODELS = ["llama3.1:8b", "qwen2.5-coder:7b"];

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [model, setModel] = useState(MODELS[0]);
  const [labLoading, setLabLoading] = useState(false);
  const [labError, setLabError] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("inactive");

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

  useEffect(() => {
    if (labActive && fwConnected) {
      setConnectionStatus((prev) => (prev === "blocked" ? "blocked" : "active"));
    } else {
      setConnectionStatus("inactive");
    }
  }, [labActive, fwConnected]);

  async function handleStartLab() {
    setLabLoading(true);
    setLabError(null);
    try {
      await startLab(SCENARIO);
      await pollHealth();
      setConnectionStatus("active");
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
      setConnectionStatus("inactive");
    } catch (err) {
      setLabError(String(err));
    } finally {
      setLabLoading(false);
    }
  }

  function handleToolCall(toolName: string) {
    if (toolName === "block_traffic") {
      setConnectionStatus("blocked");
    } else if (toolName === "flush_rules" || toolName === "allow_traffic") {
      setConnectionStatus("active");
    }
  }

  return (
    <div className="h-screen flex flex-col bg-white">
      <div className="flex-1 flex min-h-0">
        {/* Topology pane */}
        <div className="w-1/2 border-r border-gray-200">
          <div className="px-4 py-3 border-b border-gray-200">
            <h2 className="text-sm font-semibold text-gray-700">Network Topology</h2>
          </div>
          <div className="h-[calc(100%-49px)]">
            <TopologyPane connectionStatus={connectionStatus} />
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
            <ChatPane model={model} labActive={labActive && fwConnected} onToolCall={handleToolCall} />
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
        </div>

        {labError && (
          <span className="text-xs text-red-500 ml-auto">{labError}</span>
        )}
      </div>
    </div>
  );
}
