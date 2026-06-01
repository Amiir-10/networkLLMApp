import { useCallback, useEffect, useMemo, useState } from "react";
import TopologyPane from "../components/TopologyPane";
import ConsoleTerminal from "../components/ConsoleTerminal";
import {
  fetchHealth,
  fetchRules,
  addRule,
  type ParsedRule,
} from "../api";
import { INITIAL_NODES, FIREWALL_NODE_ID, ipToNodeLabel } from "../topology";

const SCENARIO = "central-hub";
const NODE_IDS = INITIAL_NODES.map((n) => n.id);
const PROTOS = ["icmp", "tcp", "udp"];

function roleOf(nodeId: string): string {
  return INITIAL_NODES.find((n) => n.id === nodeId)?.data.role ?? "pc";
}

// Read-only firewall rule view + an add-rule form, both wired to the same
// GET/POST /rules the `/` view uses. A rule added here mirrors into `/`.
function FirewallPanel({
  dropRules,
  onChanged,
}: {
  dropRules: ParsedRule[];
  onChanged: () => void;
}) {
  const others = NODE_IDS.filter((n) => n !== FIREWALL_NODE_ID);
  const [src, setSrc] = useState(others[0]);
  const [dst, setDst] = useState(others[1] ?? others[0]);
  const [proto, setProto] = useState(PROTOS[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await addRule(src, dst, proto);
      onChanged();
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-b border-gray-200 p-3 text-xs space-y-3">
      <div>
        <div className="font-semibold text-gray-700 mb-1">Active DROP rules</div>
        {dropRules.length === 0 ? (
          <div className="text-gray-400">none</div>
        ) : (
          <ul className="space-y-1">
            {dropRules.map((r) => (
              <li
                key={r.raw}
                className="font-mono text-[11px] px-1.5 py-0.5 bg-red-50 border border-red-200 text-red-700 rounded"
              >
                {ipToNodeLabel(r.src_ip)} → {ipToNodeLabel(r.dst_ip)}
                {r.proto ? ` (${r.proto}${r.port ? `:${r.port}` : ""})` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={submit} className="space-y-2">
        <div className="font-semibold text-gray-700">Add DROP rule</div>
        <div className="flex items-center gap-1.5">
          <select value={src} onChange={(e) => setSrc(e.target.value)} className="border border-gray-300 rounded px-1.5 py-1 bg-white">
            {others.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <span className="text-gray-400">→</span>
          <select value={dst} onChange={(e) => setDst(e.target.value)} className="border border-gray-300 rounded px-1.5 py-1 bg-white">
            {others.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <select value={proto} onChange={(e) => setProto(e.target.value)} className="border border-gray-300 rounded px-1.5 py-1 bg-white">
            {PROTOS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button
            type="submit"
            disabled={busy || src === dst}
            className="bg-red-600 text-white px-2.5 py-1 rounded font-medium disabled:opacity-40 hover:bg-red-700 transition-colors"
          >
            {busy ? "…" : "Block"}
          </button>
        </div>
        {src === dst && <div className="text-gray-400">source and destination must differ</div>}
        {err && <div className="text-red-500">{err}</div>}
      </form>
    </div>
  );
}

export default function ConsolePage() {
  const [labReady, setLabReady] = useState(false);
  const [rules, setRules] = useState<ParsedRule[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const pollHealth = useCallback(async () => {
    try {
      const h = await fetchHealth();
      setLabReady(h.lab_active && h.firewall_connected);
    } catch {
      setLabReady(false);
    }
  }, []);

  const refetchRules = useCallback(async () => {
    try {
      setRules((await fetchRules()).parsed);
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    pollHealth();
    const id = setInterval(pollHealth, 5000);
    return () => clearInterval(id);
  }, [pollHealth]);

  useEffect(() => {
    if (labReady) refetchRules();
    else setRules([]);
  }, [labReady, refetchRules]);

  const dropRules = useMemo(() => rules.filter((r) => r.action === "drop"), [rules]);

  return (
    <div className="h-screen flex flex-col bg-white">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">
          Console — click a node for a live shell
        </h2>
        <span className="text-xs text-gray-400">Scenario: {SCENARIO}</span>
      </div>

      <div className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0">
          <TopologyPane
            labReady={labReady}
            dropRules={dropRules}
            pingEvent={null}
            onPingEventComplete={() => {}}
            onNodeClick={setSelected}
          />
        </div>

        {selected && (
          <div className="w-[460px] border-l border-gray-200 flex flex-col min-h-0">
            <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between bg-gray-50">
              <span className="text-sm font-semibold text-gray-700">
                {selected}
                <span className="ml-2 text-xs font-normal text-gray-400">{roleOf(selected)}</span>
              </span>
              <button
                onClick={() => setSelected(null)}
                className="text-gray-400 hover:text-gray-700 text-sm"
                title="Close"
              >
                ✕
              </button>
            </div>

            {selected === FIREWALL_NODE_ID && (
              <FirewallPanel dropRules={dropRules} onChanged={refetchRules} />
            )}

            <div className="flex-1 min-h-0">
              {/* key remounts the terminal (fresh ws/PTY) when the node changes */}
              <ConsoleTerminal key={selected} scenario={SCENARIO} node={selected} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
