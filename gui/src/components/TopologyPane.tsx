import { createContext, useContext, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  type Node,
  type Edge,
  type NodeProps,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  INITIAL_NODES,
  FIREWALL_NODE_ID,
  ipToNodeLabel,
  wireIdForHop,
  type DeviceNodeData,
} from "../topology";
import type { ParsedRule } from "../api";
import type { PingEvent } from "../App";

// --- SVG Icons ---

function PcIcon() {
  return (
    <svg width="36" height="32" viewBox="0 0 36 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="1" width="32" height="22" rx="2" stroke="#64748b" strokeWidth="2" fill="#f8fafc" />
      <rect x="6" y="5" width="24" height="14" rx="1" fill="#e2e8f0" />
      <rect x="13" y="24" width="10" height="3" fill="#94a3b8" />
      <rect x="10" y="27" width="16" height="2" rx="1" fill="#94a3b8" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg width="36" height="40" viewBox="0 0 36 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M18 2 L4 10 L4 22 C4 30 10 36 18 38 C26 36 32 30 32 22 L32 10 Z"
        stroke="#64748b"
        strokeWidth="2"
        fill="#f8fafc"
      />
      <path
        d="M18 10 L18 26 M12 18 L24 18"
        stroke="#94a3b8"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function RouterIcon() {
  return (
    <svg width="40" height="32" viewBox="0 0 40 32" fill="none" xmlns="http://www.w3.org/2000/svg">
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

// --- Contexts (lab readiness + DROP rules without forcing nodes/edges re-creation) ---

const LabReadyContext = createContext<boolean>(false);
const DropRulesContext = createContext<ParsedRule[]>([]);

// --- Custom Node ---

const handleStyle = { background: "transparent", border: "none", width: 1, height: 1 };

function ruleChipLabel(rule: ParsedRule): string {
  const src = ipToNodeLabel(rule.src_ip);
  const dst = ipToNodeLabel(rule.dst_ip);
  let suffix = "";
  if (rule.proto) {
    suffix = rule.port ? ` (${rule.proto}:${rule.port})` : ` (${rule.proto})`;
  }
  return `${src} → ${dst}${suffix}`;
}

function DeviceNode({ data }: NodeProps) {
  const { label, ip, role } = data as DeviceNodeData;
  const dropRules = useContext(DropRulesContext);

  let icon: React.ReactNode;
  if (role === "firewall") icon = <ShieldIcon />;
  else if (role === "router") icon = <RouterIcon />;
  else icon = <PcIcon />;

  const isFirewall = role === "firewall";

  return (
    <div className="flex flex-col items-center gap-1 cursor-grab active:cursor-grabbing relative">
      {/* Four-direction handles so edges can attach on any side; each acts as both source and target. */}
      <Handle id="t" type="target" position={Position.Top} style={handleStyle} />
      <Handle id="r" type="target" position={Position.Right} style={handleStyle} />
      <Handle id="b" type="target" position={Position.Bottom} style={handleStyle} />
      <Handle id="l" type="target" position={Position.Left} style={handleStyle} />
      <Handle id="ts" type="source" position={Position.Top} style={handleStyle} />
      <Handle id="rs" type="source" position={Position.Right} style={handleStyle} />
      <Handle id="bs" type="source" position={Position.Bottom} style={handleStyle} />
      <Handle id="ls" type="source" position={Position.Left} style={handleStyle} />
      {icon}
      <span className="text-xs font-semibold text-gray-700">{label}</span>
      <span className="text-[10px] text-gray-400 font-mono">{ip}</span>

      {isFirewall && dropRules.length > 0 && (
        <div
          className="absolute top-full left-1/2 -translate-x-1/2 mt-2 flex flex-col gap-1 items-center pointer-events-none"
          style={{ zIndex: 5 }}
        >
          <span className="text-[9px] uppercase tracking-wide text-red-600/70 font-semibold">
            Active DROP
          </span>
          {dropRules.slice(0, 6).map((r) => (
            <div
              key={r.raw}
              className="px-1.5 py-0.5 bg-red-50 border border-red-300 text-red-700 rounded text-[10px] font-mono whitespace-nowrap shadow-sm"
            >
              {ruleChipLabel(r)}
            </div>
          ))}
          {dropRules.length > 6 && (
            <span className="text-[9px] text-red-500">+{dropRules.length - 6} more</span>
          )}
        </div>
      )}
    </div>
  );
}

// --- Wire Edge ---
//
// The wire is a *physical-link-up* indicator. It NEVER reflects firewall policy.
//   - lab down            -> muted dashed, static.
//   - lab up              -> green moving dash ("network is alive").
//   - lab up + highlighted -> brighter/thicker green, faster dash (carries an active ping).
// A blocked ping does NOT turn the wire red; the only "blocked here" cue is a
// small stop glyph near the firewall end (data.stopMarker).

interface WireEdgeData {
  highlight?: boolean;
  stopMarker?: boolean;
  [key: string]: unknown;
}

function WireEdge({
  id,
  source,
  sourceX,
  sourceY,
  targetX,
  targetY,
  label,
  data,
}: EdgeProps) {
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

  // The firewall is always one endpoint of every physical link; place the stop
  // glyph a little inside the wire from the firewall end so it never lands on
  // the shield icon or its chip cluster.
  const fwIsSource = source === FIREWALL_NODE_ID;
  const fwX = fwIsSource ? sourceX : targetX;
  const fwY = fwIsSource ? sourceY : targetY;
  const otherX = fwIsSource ? targetX : sourceX;
  const otherY = fwIsSource ? targetY : sourceY;
  const dx = otherX - fwX;
  const dy = otherY - fwY;
  const len = Math.hypot(dx, dy) || 1;
  const stopX = fwX + (dx / len) * 18;
  const stopY = fwY + (dy / len) * 18;

  return (
    <>
      <path
        id={id}
        d={edgePath}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeDasharray={dashArray}
        fill="none"
        opacity={opacity}
        className={animationClass}
      />
      {label && (
        <text
          x={midX}
          y={midY - 8}
          textAnchor="middle"
          className="fill-gray-400"
          style={{ fontSize: 9 }}
        >
          {String(label)}
        </text>
      )}
      {d.stopMarker && (
        <g transform={`translate(${stopX}, ${stopY})`}>
          <animate
            attributeName="opacity"
            values="0;1;1;0"
            keyTimes="0;0.15;0.7;1"
            dur="900ms"
            fill="freeze"
          />
          <circle r="6" fill="#fee2e2" stroke="#ef4444" strokeWidth="1.5" />
          <line x1="-3.5" y1="-3.5" x2="3.5" y2="3.5" stroke="#ef4444" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      )}
    </>
  );
}

// --- Node/Edge type registries ---

const nodeTypes = { device: DeviceNode };
const edgeTypes = { wire: WireEdge };

// --- Static wire edges (one per physical link in central-hub) ---

const STATIC_EDGES: Edge[] = [
  {
    id: "pc1-fw",
    source: "pc1",
    sourceHandle: "bs",
    target: "fw",
    targetHandle: "t",
    type: "wire",
    label: "10.99.10.0/24",
  },
  {
    id: "fw-pc2",
    source: "fw",
    sourceHandle: "rs",
    target: "pc2",
    targetHandle: "l",
    type: "wire",
    label: "10.99.20.0/24",
  },
  {
    id: "fw-pc3",
    source: "fw",
    sourceHandle: "bs",
    target: "pc3",
    targetHandle: "t",
    type: "wire",
    label: "10.99.30.0/24",
  },
  {
    id: "router-fw",
    source: "router",
    sourceHandle: "rs",
    target: "fw",
    targetHandle: "l",
    type: "wire",
    label: "203.0.113.0/24 (WAN)",
  },
];

// --- Ping highlight: map a ping event onto the existing wires it traverses ---

const HOP_MS = 850;

// All PC/router traffic flows through fw. Resolve the wire(s) a ping crosses:
//   src -> fw  (hop 1, always)        and   fw -> dst  (hop 2, pass only)
// If an endpoint is fw itself, it collapses to a single hop.
function resolveHopWires(src: string, dst: string): { first: string | null; second: string | null } {
  if (src === FIREWALL_NODE_ID) {
    return { first: wireIdForHop(FIREWALL_NODE_ID, dst), second: null };
  }
  if (dst === FIREWALL_NODE_ID) {
    return { first: wireIdForHop(src, FIREWALL_NODE_ID), second: null };
  }
  return {
    first: wireIdForHop(src, FIREWALL_NODE_ID),
    second: wireIdForHop(FIREWALL_NODE_ID, dst),
  };
}

interface WireHighlight {
  stopMarker: boolean;
}

// --- Component ---

interface Props {
  labReady: boolean;
  dropRules: ParsedRule[];
  pingEvent: PingEvent | null;
  onPingEventComplete: () => void;
  // Optional: when provided, clicking a node calls this with its id. Used by the
  // /console page to open a per-node shell; omitted on the main `/` view (no-op).
  onNodeClick?: (nodeId: string) => void;
}

export default function TopologyPane({
  labReady,
  dropRules,
  pingEvent,
  onPingEventComplete,
  onNodeClick,
}: Props) {
  const [nodes, , onNodesChange] = useNodesState<Node<DeviceNodeData>>(INITIAL_NODES);
  const [highlights, setHighlights] = useState<Record<string, WireHighlight>>({});

  // Stage the highlight along the path: source-side wire lights first, then the
  // far-side wire ~HOP_MS later (pass only), telling the "packet flows through"
  // story without drawing any ghost edges. One timer per stage; cleared on change.
  useEffect(() => {
    if (!pingEvent) {
      setHighlights({});
      return;
    }
    const { src, dst, blocked } = pingEvent;
    const { first, second } = resolveHopWires(src, dst);

    const initial: Record<string, WireHighlight> = {};
    if (first) initial[first] = { stopMarker: blocked };
    setHighlights(initial);

    const timers: ReturnType<typeof setTimeout>[] = [];
    if (!blocked && second) {
      timers.push(
        setTimeout(() => {
          setHighlights((h) => ({ ...h, [second]: { stopMarker: false } }));
        }, HOP_MS)
      );
    }

    const hops = blocked ? 1 : 2;
    const total = HOP_MS * hops + 400; // leave the stop glyph / final hop visible
    timers.push(setTimeout(onPingEventComplete, total));

    return () => timers.forEach(clearTimeout);
  }, [pingEvent, onPingEventComplete]);

  const edges: Edge[] = useMemo(
    () =>
      STATIC_EDGES.map((e) => {
        const h = highlights[e.id];
        if (!h) return e;
        return { ...e, data: { highlight: true, stopMarker: h.stopMarker } };
      }),
    [highlights]
  );

  return (
    <LabReadyContext.Provider value={labReady}>
      <DropRulesContext.Provider value={dropRules}>
        <div className="h-full w-full">
          <style>{`
            @keyframes dash-flow { to { stroke-dashoffset: -24; } }
            .edge-flow      { animation: dash-flow 1.5s linear infinite; }
            .edge-flow-fast { animation: dash-flow 0.6s linear infinite; }
          `}</style>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onNodeClick={onNodeClick ? (_, n) => onNodeClick(n.id) : undefined}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            nodesDraggable={true}
            nodesConnectable={false}
            elementsSelectable={true}
            panOnDrag={true}
            zoomOnScroll={true}
            zoomOnDoubleClick={true}
            minZoom={0.3}
            maxZoom={3}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} color="#f1f5f9" />
            <Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        </div>
      </DropRulesContext.Provider>
    </LabReadyContext.Provider>
  );
}
