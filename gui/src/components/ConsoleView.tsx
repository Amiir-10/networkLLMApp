import { useEffect, useMemo, useState } from "react";
import TopologyPane from "./TopologyPane";
import ConsoleTerminal from "./ConsoleTerminal";
import { addRule, flushRules, type ParsedRule } from "../api";
import { ipToNodeLabel, type BuiltTopology } from "../topology";

const PROTOS = ["icmp", "tcp", "udp"];

// A drag-to-resize size (px) persisted to localStorage, so a pane the user
// sized stays that way across reloads — same durability the console sessions
// have (supervisor point 8). Returns the size and a pointer-down handler for
// the drag handle.
function usePersistentSize(
  key: string, initial: number, min: number, max: number,
  axis: "x" | "y", invert = false,
) {
  const [size, setSize] = useState<number>(() => {
    try {
      const v = localStorage.getItem(key);
      if (v) return Math.min(max, Math.max(min, Number(v)));
    } catch { /* private mode / blocked storage */ }
    return initial;
  });
  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    const start = axis === "x" ? e.clientX : e.clientY;
    const startSize = size;
    const move = (ev: PointerEvent) => {
      const cur = axis === "x" ? ev.clientX : ev.clientY;
      const delta = (invert ? -1 : 1) * (cur - start);
      setSize(Math.min(max, Math.max(min, startSize + delta)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setSize((s) => { try { localStorage.setItem(key, String(s)); } catch { /* ignore */ } return s; });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return [size, onPointerDown] as const;
}

// Typed + run automatically when a firewall's shell opens: the firewalld
// forward policy (fwd-filter) is where every block/allow rich rule lives —
// this is the real "show me the current rules" command (supervisor request).
const FW_RULES_CMD = "firewall-cmd --policy=fwd-filter --list-rich-rules";

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
  const [clearing, setClearing] = useState(false);

  async function clearRules() {
    if (!window.confirm(`Clear ALL rules on ${firewall}? Blocked traffic starts flowing again.`)) return;
    setClearing(true);
    setErr(null);
    try {
      await flushRules(firewall);
      onChanged();
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setClearing(false);
    }
  }

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
        <div className="flex items-center justify-between mb-1">
          <span className="font-semibold text-gray-700">Active DROP rules ({firewall})</span>
          <button
            onClick={clearRules}
            disabled={clearing || myDrops.length === 0}
            title={myDrops.length === 0 ? "No rules to clear" : `Remove every rule on ${firewall}`}
            className="text-red-600 border border-red-300 px-2 py-0.5 rounded font-medium disabled:opacity-40 hover:bg-red-50 transition-colors"
          >
            {clearing ? "…" : "Clear rules"}
          </button>
        </div>
        {myDrops.length === 0 ? (
          <div className="text-gray-400">none</div>
        ) : (
          <ul className="space-y-1">
            {myDrops.map((r) => (
              <li key={r.raw} className="font-mono text-xs px-1.5 py-0.5 bg-red-50 border border-red-200 text-red-700 rounded">
                {ipToNodeLabel(ipMap, r.src_ip)} → {r.dst_name ?? ipToNodeLabel(ipMap, r.dst_ip)}
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
  vulnerableNodes: string[];
  refetchRules: () => void;
}

// The debug console: full-screen topology; click a node for a live PTY shell
// (firewalls also get a rule view/add panel). Shares the shell's topology, so
// it shows the selected scenario even before a lab is started.
//
// Console sessions PERSIST (supervisor request 2026-08-25, point 2): every
// node whose shell was opened keeps its ConsoleTerminal mounted (hidden when
// another node is selected), so its PTY, scrollback and running commands
// survive switching nodes — and switching tabs, since App keeps this view
// mounted. ✕ deselects but keeps the session; sessions reset when the lab
// goes down (the PTYs are dead then anyway).
export default function ConsoleView({ topology, labReady, scenario, dropRules, vulnerableNodes, refetchRules }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [openConsoles, setOpenConsoles] = useState<string[]>([]);
  // Resizable, persisted panes (point 8): the console panel's width, and — when
  // a firewall shell is open — the height of the rules list above the terminal,
  // so a firewall with MANY rules can't push the shell out of view.
  const [panelWidth, startWidthDrag] = usePersistentSize("console.panelWidth", 448, 320, 900, "x", true);
  const [rulesHeight, startRulesDrag] = usePersistentSize("console.rulesHeight", 200, 72, 520, "y");

  // Lab stopped/reset: every PTY died with its container — drop the sessions
  // so reopening a node starts a fresh shell instead of a dead terminal.
  useEffect(() => {
    if (!labReady) {
      setOpenConsoles([]);
      setSelected(null);
    }
  }, [labReady]);

  const selectNode = (id: string) => {
    setSelected(id);
    if (labReady) {
      setOpenConsoles((open) => (open.includes(id) ? open : [...open, id]));
    }
  };

  const nodeIds = useMemo(
    () => (topology ? topology.deviceNodes.filter((n) => n.data.role !== "switch").map((n) => n.id) : []),
    [topology]
  );
  const selectedRole = useMemo(
    () => topology?.deviceNodes.find((n) => n.id === selected)?.data.role ?? "pc",
    [topology, selected]
  );
  const roleOf = (id: string) =>
    topology?.deviceNodes.find((n) => n.id === id)?.data.role ?? "pc";

  return (
    <div className="flex-1 flex min-h-0">
      <div className="flex-1 min-w-0 relative">
        <TopologyPane
          topology={topology}
          labReady={labReady}
          dropRules={dropRules}
          vulnerableNodes={vulnerableNodes}
          pingEvent={null}
          onPingEventComplete={() => {}}
          onNodeClick={selectNode}
        />
        {!labReady && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 text-xs text-gray-400 bg-white/80 border border-gray-200 rounded-full px-3 py-1 pointer-events-none">
            Start a lab to open live shells — this is a preview of the topology
          </div>
        )}
      </div>

      {/* Drag handle to resize the console panel width (point 8). */}
      {selected && (
        <div
          onPointerDown={startWidthDrag}
          title="Drag to resize the console panel"
          className="w-1.5 shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 transition-colors"
        />
      )}

      {/* The panel is hidden — not unmounted — when no node is selected, so
          the open console sessions inside keep running. */}
      <div
        style={selected ? { width: panelWidth } : undefined}
        className={selected ? "shrink-0 border-l border-gray-200 flex flex-col min-h-0" : "hidden"}
      >
          <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between bg-gray-50">
            <span className="text-sm font-semibold text-gray-700">
              {selected}
              <span className="ml-2 text-xs font-normal text-gray-400">{selectedRole}</span>
            </span>
            <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-700 text-sm" title="Hide panel (the shell keeps running)">✕</button>
          </div>

          {labReady && scenario ? (
            <>
              {selected && selectedRole === "firewall" && (
                <>
                  {/* Rules list gets its OWN resizable, scrollable band so many
                      rules never push the shell below out of view (point 8). */}
                  <div style={{ height: rulesHeight }} className="shrink-0 overflow-y-auto">
                    <FirewallPanel
                      firewall={selected}
                      nodeIds={nodeIds}
                      ipMap={topology?.ipToNodeId ?? {}}
                      dropRules={dropRules}
                      onChanged={refetchRules}
                    />
                  </div>
                  <div
                    onPointerDown={startRulesDrag}
                    title="Drag to resize the rules / shell split"
                    className="h-1.5 shrink-0 cursor-row-resize bg-gray-200 hover:bg-blue-400 transition-colors"
                  />
                </>
              )}
              {/* EVERY opened console stays mounted; only the selected one is
                  visible. Keyed per node so each keeps its own ws/PTY/history.
                  Firewalls auto-run the show-rules command when first opened. */}
              {openConsoles.map((id) => (
                <div key={id} className={id === selected ? "flex-1 min-h-0" : "hidden"}>
                  <ConsoleTerminal
                    scenario={scenario}
                    node={id}
                    autoCommand={roleOf(id) === "firewall" ? FW_RULES_CMD : undefined}
                  />
                </div>
              ))}
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center p-6 text-center text-xs text-gray-400">
              Start the lab to open a live shell on <span className="font-mono mx-1">{selected}</span>.
            </div>
          )}
      </div>
    </div>
  );
}
