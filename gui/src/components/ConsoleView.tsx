import { useMemo, useState } from "react";
import TopologyPane from "./TopologyPane";
import ConsoleTerminal from "./ConsoleTerminal";
import { addRule, type ParsedRule } from "../api";
import { ipToNodeLabel, type BuiltTopology } from "../topology";

const PROTOS = ["icmp", "tcp", "udp"];

// Read-only rule view + add-rule form for ONE firewall (the clicked node):
// rules filtered to this firewall, an added rule explicitly targets it.
function FirewallPanel({
  firewall,
  nodeIds,
  ipMap,
  dropRules,
  onChanged,
}: {
  firewall: string;
  nodeIds: string[];
  ipMap: Record<string, string>;
  dropRules: ParsedRule[];
  onChanged: () => void;
}) {
  const others = nodeIds.filter((n) => n !== firewall);
  const [src, setSrc] = useState(others[0] ?? "");
  const [dst, setDst] = useState(others[1] ?? others[0] ?? "");
  const [proto, setProto] = useState(PROTOS[0]);
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const portable = proto === "tcp" || proto === "udp";
  const myDrops = dropRules.filter((r) => (r.firewall ?? firewall) === firewall);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const p = portable && port.trim() !== "" ? Number(port) : null;
      await addRule(src, dst, proto, p, firewall);
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
        <div className="font-semibold text-gray-700 mb-1">Active DROP rules ({firewall})</div>
        {myDrops.length === 0 ? (
          <div className="text-gray-400">none</div>
        ) : (
          <ul className="space-y-1">
            {myDrops.map((r) => (
              <li key={r.raw} className="font-mono text-xs px-1.5 py-0.5 bg-red-50 border border-red-200 text-red-700 rounded">
                {ipToNodeLabel(ipMap, r.src_ip)} → {ipToNodeLabel(ipMap, r.dst_ip)}
                {r.proto ? ` (${r.proto}${r.port ? `:${r.port}` : ""})` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={submit} className="space-y-2">
        <div className="font-semibold text-gray-700">Add DROP rule on {firewall}</div>
        <div className="flex flex-wrap items-center gap-1.5">
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
          {portable && (
            <input type="number" value={port} onChange={(e) => setPort(e.target.value)} placeholder="port" className="border border-gray-300 rounded px-1.5 py-1 bg-white w-20" />
          )}
          <button type="submit" disabled={busy || src === dst} className="bg-red-600 text-white px-2.5 py-1 rounded font-medium disabled:opacity-40 hover:bg-red-700 transition-colors">
            {busy ? "…" : "Block"}
          </button>
        </div>
        {src === dst && <div className="text-gray-400">source and destination must differ</div>}
        {err && <div className="text-red-500">{err}</div>}
      </form>
    </div>
  );
}

interface Props {
  topology: BuiltTopology | null;
  labReady: boolean;
  scenario: string | null;
  dropRules: ParsedRule[];
  refetchRules: () => void;
}

// The debug console: full-screen topology; click a node for a live PTY shell
// (firewalls also get a rule view/add panel). Shares the shell's topology, so
// it shows the selected scenario even before a lab is started.
export default function ConsoleView({ topology, labReady, scenario, dropRules, refetchRules }: Props) {
  const [selected, setSelected] = useState<string | null>(null);

  const nodeIds = useMemo(
    () => (topology ? topology.deviceNodes.filter((n) => n.data.role !== "switch").map((n) => n.id) : []),
    [topology]
  );
  const selectedRole = useMemo(
    () => topology?.deviceNodes.find((n) => n.id === selected)?.data.role ?? "pc",
    [topology, selected]
  );

  return (
    <div className="flex-1 flex min-h-0">
      <div className="flex-1 min-w-0 relative">
        <TopologyPane
          topology={topology}
          labReady={labReady}
          dropRules={dropRules}
          pingEvent={null}
          onPingEventComplete={() => {}}
          onNodeClick={setSelected}
        />
        {!labReady && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 text-xs text-gray-400 bg-white/80 border border-gray-200 rounded-full px-3 py-1 pointer-events-none">
            Start a lab to open live shells — this is a preview of the topology
          </div>
        )}
      </div>

      {selected && (
        <div className="w-[28rem] border-l border-gray-200 flex flex-col min-h-0">
          <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between bg-gray-50">
            <span className="text-sm font-semibold text-gray-700">
              {selected}
              <span className="ml-2 text-xs font-normal text-gray-400">{selectedRole}</span>
            </span>
            <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-700 text-sm" title="Close">✕</button>
          </div>

          {labReady && scenario ? (
            <>
              {selectedRole === "firewall" && (
                <FirewallPanel
                  firewall={selected}
                  nodeIds={nodeIds}
                  ipMap={topology?.ipToNodeId ?? {}}
                  dropRules={dropRules}
                  onChanged={refetchRules}
                />
              )}
              <div className="flex-1 min-h-0">
                {/* key remounts the terminal (fresh ws/PTY) when the node changes */}
                <ConsoleTerminal key={selected} scenario={scenario} node={selected} />
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center p-6 text-center text-xs text-gray-400">
              Start the lab to open a live shell on <span className="font-mono mx-1">{selected}</span>.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
