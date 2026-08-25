import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { ipToNodeLabel, type DeviceNodeData, type BuiltTopology } from "../topology";
import type { ParsedRule, PingEvent } from "../api";

// --- SVG Icons ---

function PcIcon() {
  return (
    <svg width="54" height="48" viewBox="0 0 36 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="1" width="32" height="22" rx="2" stroke="#64748b" strokeWidth="2" fill="#f8fafc" />
      <rect x="6" y="5" width="24" height="14" rx="1" fill="#e2e8f0" />
      <rect x="13" y="24" width="10" height="3" fill="#94a3b8" />
      <rect x="10" y="27" width="16" height="2" rx="1" fill="#94a3b8" />
    </svg>
  );
}

function ShieldIcon() {
  // Blue firewall (supervisor request 2026-08-25, point 3).
  return (
    <svg width="50" height="56" viewBox="0 0 36 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M18 2 L4 10 L4 22 C4 30 10 36 18 38 C26 36 32 30 32 22 L32 10 Z" stroke="#2563eb" strokeWidth="2" fill="#dbeafe" />
      <path d="M18 10 L18 26 M12 18 L24 18" stroke="#3b82f6" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

function RouterIcon() {
  return (
    <svg width="58" height="46" viewBox="0 0 40 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="14" width="36" height="14" rx="2" stroke="#64748b" strokeWidth="2" fill="#f8fafc" />
      <circle cx="8" cy="21" r="1.5" fill="#22c55e" />
      <circle cx="14" cy="21" r="1.5" fill="#22c55e" />
      <circle cx="20" cy="21" r="1.5" fill="#94a3b8" />
      <circle cx="26" cy="21" r="1.5" fill="#94a3b8" />
      <path d="M12 14 L12 8 M20 14 L20 4 M28 14 L28 8" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M9 9 Q12 6 15 9 M17 5 Q20 1 23 5 M25 9 Q28 6 31 9" stroke="#94a3b8" strokeWidth="1.2" fill="none" strokeLinecap="round" />
    </svg>
  );
}

function SwitchIcon() {
  return (
    <svg width="58" height="40" viewBox="0 0 40 28" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="8" width="36" height="14" rx="2" stroke="#64748b" strokeWidth="2" fill="#f8fafc" />
      <path d="M9 12 L13 12 M9 18 L13 18 M18 12 L22 12 M18 18 L22 18 M27 12 L31 12 M27 18 L31 18" stroke="#94a3b8" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M6 8 L10 4 M16 8 L20 4 M26 8 L30 4" stroke="#cbd5e1" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

// --- Contexts (avoid re-creating nodes/edges on every readiness/rule change) ---

const LabReadyContext = createContext<boolean>(false);
const DropRulesContext = createContext<ParsedRule[]>([]);
const IpMapContext = createContext<Record<string, string>>({});

const handleStyle = { background: "transparent", border: "none", width: 1, height: 1 };

function ruleChipLabel(rule: ParsedRule, ipMap: Record<string, string>): string {
  const src = ipToNodeLabel(ipMap, rule.src_ip);
  const dst = rule.dst_name ?? ipToNodeLabel(ipMap, rule.dst_ip);
  let suffix = "";
  if (rule.proto) suffix = rule.port ? ` (${rule.proto}:${rule.port})` : ` (${rule.proto})`;
  return `${src} → ${dst}${suffix}`;
}

// --- Cloud (subnet) node — non-interactive background region ---

function CloudNode({ data }: NodeProps) {
  const { label } = data as { label: string };
  return (
    <div
      className="w-full h-full rounded-2xl border-2 border-dashed border-sky-300/70 bg-sky-50/40 pointer-events-none"
      style={{ boxSizing: "border-box" }}
    >
      <span className="absolute top-1.5 left-3 text-sm font-mono font-semibold text-sky-500/80 tracking-wide">
        {label}
      </span>
    </div>
  );
}

// --- Device node ---

function DeviceNode({ data }: NodeProps) {
  const { label, ip, role } = data as DeviceNodeData;
  const dropRules = useContext(DropRulesContext);
  const ipMap = useContext(IpMapContext);

  let icon: React.ReactNode;
  if (role === "firewall") icon = <ShieldIcon />;
  else if (role === "router") icon = <RouterIcon />;
  else if (role === "switch") icon = <SwitchIcon />;
  else icon = <PcIcon />;

  // Drops are shown under the firewall that enforces them (rule.firewall).
  const myDrops =
    role === "firewall" ? dropRules.filter((r) => (r.firewall ?? label) === label) : [];

  return (
    <div className="flex flex-col items-center gap-1 cursor-grab active:cursor-grabbing relative">
      <Handle id="t" type="target" position={Position.Top} style={handleStyle} />
      <Handle id="r" type="target" position={Position.Right} style={handleStyle} />
      <Handle id="b" type="target" position={Position.Bottom} style={handleStyle} />
      <Handle id="l" type="target" position={Position.Left} style={handleStyle} />
      <Handle id="ts" type="source" position={Position.Top} style={handleStyle} />
      <Handle id="rs" type="source" position={Position.Right} style={handleStyle} />
      <Handle id="bs" type="source" position={Position.Bottom} style={handleStyle} />
      <Handle id="ls" type="source" position={Position.Left} style={handleStyle} />
      {icon}
      {/* Bigger node labels (supervisor request 2026-08-25, point 1). */}
      <span className="text-2xl font-semibold text-gray-700">{label}</span>
      {ip && <span className="text-base text-gray-400 font-mono">{ip}</span>}

      {myDrops.length > 0 && (
        <div
          className="absolute top-full left-1/2 -translate-x-1/2 mt-2 flex flex-col gap-1 items-center pointer-events-none"
          style={{ zIndex: 5 }}
        >
          <span className="text-xs uppercase tracking-wide text-red-600/70 font-semibold">Active DROP</span>
          {myDrops.slice(0, 6).map((r) => (
            <div
              key={r.raw}
              className="px-2 py-0.5 bg-red-50 border border-red-300 text-red-700 rounded text-xs font-mono whitespace-nowrap shadow-sm"
            >
              {ruleChipLabel(r, ipMap)}
            </div>
          ))}
          {myDrops.length > 6 && <span className="text-xs text-red-500">+{myDrops.length - 6} more</span>}
        </div>
      )}
    </div>
  );
}

// --- Wire edge: physical-link-up indicator (never reflects firewall policy) ---

interface WireEdgeData {
  highlight?: boolean;
  stopMarker?: boolean;
  [key: string]: unknown;
}

function WireEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps) {
  const labReady = useContext(LabReadyContext);
  const d = (data ?? {}) as WireEdgeData;
  const edgePath = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`;
  const midX = (sourceX + targetX) / 2;
  const midY = (sourceY + targetY) / 2;

  let strokeColor: string;
  let strokeWidth = 2;
  let dashArray: string | undefined;
  let opacity = 1;
  let animationClass = "";

  if (!labReady) {
    strokeColor = "#cbd5e1";
    dashArray = "4 4";
    opacity = 0.55;
  } else if (d.highlight) {
    strokeColor = "#16a34a";
    strokeWidth = 3;
    dashArray = "8 4";
    animationClass = "edge-flow-fast";
  } else {
    strokeColor = "#22c55e";
    dashArray = "8 4";
    animationClass = "edge-flow";
  }

  return (
    <>
      <path id={id} d={edgePath} stroke={strokeColor} strokeWidth={strokeWidth} strokeDasharray={dashArray} fill="none" opacity={opacity} className={animationClass} />
      {d.stopMarker && (
        <g transform={`translate(${midX}, ${midY})`}>
          <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.7;1" dur="900ms" fill="freeze" />
          <circle r="6" fill="#fee2e2" stroke="#ef4444" strokeWidth="1.5" />
          <line x1="-3.5" y1="-3.5" x2="3.5" y2="3.5" stroke="#ef4444" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      )}
    </>
  );
}

const nodeTypes = { device: DeviceNode, cloud: CloudNode };
const edgeTypes = { wire: WireEdge };

const HOP_MS = 850;

interface WireHighlight {
  stopMarker: boolean;
}

interface Props {
  topology: BuiltTopology | null;
  labReady: boolean;
  dropRules: ParsedRule[];
  pingEvent: PingEvent | null;
  onPingEventComplete: () => void;
  onNodeClick?: (nodeId: string) => void;
}

export default function TopologyPane(props: Props) {
  return (
    <ReactFlowProvider>
      <TopologyPaneInner {...props} />
    </ReactFlowProvider>
  );
}

function TopologyPaneInner({
  topology,
  labReady,
  dropRules,
  pingEvent,
  onPingEventComplete,
  onNodeClick,
}: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [baseEdges, setBaseEdges] = useState<Edge[]>([]);
  const [highlights, setHighlights] = useState<Record<string, WireHighlight>>({});
  const { fitView } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Rebuild RF nodes when the scenario (topology) changes. Clouds first (low z),
  // then device nodes on top.
  useEffect(() => {
    if (!topology) {
      setNodes([]);
      setBaseEdges([]);
      return;
    }
    setNodes([...topology.cloudNodes, ...topology.deviceNodes] as Node[]);
    // The fitView PROP only applies on mount — switching scenarios keeps the
    // old viewport, which crops a graph with a different extent (vertical
    // two-subnet-ixp after horizontal central-hub). Re-fit once React Flow
    // has rendered the new nodes (double rAF: state commit, then layout).
    // Edges are ALSO staged here, after the nodes have committed: handing
    // them to React Flow in the same render that first creates the nodes made
    // it silently drop edges whose named handles were not measured yet — the
    // vertical firewall↔switch / switch↔middle-PC wires were invisible on
    // first load until a node was dragged (supervisor point 9).
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setBaseEdges(topology.edges);
        fitView({ padding: 0.1, maxZoom: 1.25 });
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [topology, setNodes, fitView]);

  // The pane lives inside a tab that is HIDDEN (display:none), not unmounted,
  // when another view is active. If this pane initialised while hidden its
  // viewport was computed at 0×0 — re-fit when the wrapper first gets real
  // dimensions (width transition 0 → positive).
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    let lastWidth = el.getBoundingClientRect().width;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (lastWidth === 0 && w > 0) {
        requestAnimationFrame(() => fitView({ padding: 0.1, maxZoom: 1.25 }));
      }
      lastWidth = w;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fitView]);

  // Ping animation: highlight every wire on the BFS path in sequence. If blocked,
  // stop at (and mark) the wire arriving at the first firewall on the path.
  useEffect(() => {
    if (!pingEvent || !topology) {
      setHighlights({});
      return;
    }
    const { src, dst, blocked } = pingEvent;
    const wires = topology.pathWires(src, dst);
    if (wires.length === 0) {
      onPingEventComplete();
      return;
    }
    const path = topology.pathNodes(src, dst);
    let lastIdx = wires.length - 1;
    if (blocked) {
      const fwIdx = path.findIndex((id, i) => i > 0 && topology.firewallIds.includes(id));
      lastIdx = fwIdx > 0 ? Math.min(fwIdx - 1, wires.length - 1) : 0;
    }

    setHighlights({});
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let i = 0; i <= lastIdx; i++) {
      timers.push(
        setTimeout(() => {
          setHighlights((h) => ({ ...h, [wires[i]]: { stopMarker: blocked && i === lastIdx } }));
        }, i * HOP_MS)
      );
    }
    timers.push(setTimeout(onPingEventComplete, HOP_MS * (lastIdx + 1) + 400));
    return () => timers.forEach(clearTimeout);
  }, [pingEvent, topology, onPingEventComplete]);

  const edges: Edge[] = useMemo(() => {
    return baseEdges.map((e) => {
      const h = highlights[e.id];
      return h ? { ...e, data: { highlight: true, stopMarker: h.stopMarker } } : e;
    });
  }, [baseEdges, highlights]);

  return (
    <LabReadyContext.Provider value={labReady}>
      <DropRulesContext.Provider value={dropRules}>
        <IpMapContext.Provider value={topology?.ipToNodeId ?? {}}>
            <div className="h-full w-full" ref={wrapperRef}>
              <style>{`
                @keyframes dash-flow { to { stroke-dashoffset: -24; } }
                .edge-flow      { animation: dash-flow 1.5s linear infinite; }
                .edge-flow-fast { animation: dash-flow 0.6s linear infinite; }
              `}</style>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onNodeClick={onNodeClick ? (_, n) => { if (n.type === "device") onNodeClick(n.id); } : undefined}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                fitView
                fitViewOptions={{ padding: 0.1, maxZoom: 1.25 }}
                nodesDraggable={true}
                nodesConnectable={false}
                elementsSelectable={true}
                panOnDrag={true}
                zoomOnScroll={true}
                zoomOnDoubleClick={true}
                minZoom={0.2}
                maxZoom={3}
                proOptions={{ hideAttribution: true }}
              >
                <Background gap={20} size={1} color="#f1f5f9" />
                <Controls showInteractive={false} position="bottom-left" />
              </ReactFlow>
            </div>
        </IpMapContext.Provider>
      </DropRulesContext.Provider>
    </LabReadyContext.Provider>
  );
}
