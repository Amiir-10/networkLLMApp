import { createContext, useContext, useEffect, useMemo } from "react";
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

// --- Wire Edge (static, neutral) ---

function WireEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  label,
}: EdgeProps) {
  const labReady = useContext(LabReadyContext);
  const edgePath = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`;
  const midX = (sourceX + targetX) / 2;
  const midY = (sourceY + targetY) / 2;

  const strokeColor = labReady ? "#94a3b8" : "#cbd5e1";
  const dashArray = labReady ? undefined : "4 4";
  const opacity = labReady ? 1 : 0.55;

  return (
    <>
      <path
        id={id}
        d={edgePath}
        stroke={strokeColor}
        strokeWidth={2}
        strokeDasharray={dashArray}
        fill="none"
        opacity={opacity}
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
    </>
  );
}

// --- Packet Pulse Edge (transient animation) ---

interface PacketPulseData {
  color: "green" | "red";
  durMs: number;
  beginMs: number;
  stopMarker: boolean; // draw red X at target end (blocked stop)
  [key: string]: unknown;
}

function PacketPulseEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, data } = props;
  const d = data as PacketPulseData;
  const path = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`;
  const stroke = d.color === "red" ? "#ef4444" : "#22c55e";

  return (
    <>
      {/* Faint guide line under the animation so the route is visible while the packet moves. */}
      <path
        id={`${id}-guide`}
        d={path}
        stroke={stroke}
        strokeWidth={3}
        strokeOpacity={0.25}
        fill="none"
      />
      {/* Animated packet circle */}
      <circle r="6" fill={stroke} stroke="white" strokeWidth={1.5}>
        <animateMotion
          dur={`${d.durMs}ms`}
          begin={`${d.beginMs}ms`}
          repeatCount="1"
          fill="freeze"
          path={path}
        />
      </circle>
      {d.stopMarker && (
        <g transform={`translate(${targetX}, ${targetY})`} opacity={0}>
          <animate
            attributeName="opacity"
            from="0"
            to="1"
            dur="150ms"
            begin={`${d.beginMs + d.durMs}ms`}
            fill="freeze"
          />
          <circle r="11" fill="#fee2e2" stroke="#ef4444" strokeWidth="2" />
          <path d="M -5 -5 L 5 5 M 5 -5 L -5 5" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round" />
        </g>
      )}
    </>
  );
}

// --- Node/Edge type registries ---

const nodeTypes = { device: DeviceNode };
const edgeTypes = { wire: WireEdge, packet_pulse: PacketPulseEdge };

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

// --- Ping animation edge builder ---

const HOP_MS = 850;

function buildPingPulseEdges(event: PingEvent): Edge[] {
  // For central-hub, every PC connects through fw. Hop 1: src -> fw. Hop 2 (if pass): fw -> dst.
  // If src or dst is fw itself, collapse appropriately.
  const edges: Edge[] = [];

  if (event.src === FIREWALL_NODE_ID && event.dst !== FIREWALL_NODE_ID) {
    edges.push({
      id: `ping-pulse-${event.id}-0`,
      source: FIREWALL_NODE_ID,
      target: event.dst,
      type: "packet_pulse",
      data: { color: event.blocked ? "red" : "green", durMs: HOP_MS, beginMs: 0, stopMarker: event.blocked } as PacketPulseData,
      selectable: false,
      focusable: false,
    });
    return edges;
  }

  if (event.dst === FIREWALL_NODE_ID && event.src !== FIREWALL_NODE_ID) {
    edges.push({
      id: `ping-pulse-${event.id}-0`,
      source: event.src,
      target: FIREWALL_NODE_ID,
      type: "packet_pulse",
      data: { color: event.blocked ? "red" : "green", durMs: HOP_MS, beginMs: 0, stopMarker: event.blocked } as PacketPulseData,
      selectable: false,
      focusable: false,
    });
    return edges;
  }

  // Standard two-hop case through fw.
  edges.push({
    id: `ping-pulse-${event.id}-0`,
    source: event.src,
    target: FIREWALL_NODE_ID,
    type: "packet_pulse",
    data: {
      color: event.blocked ? "red" : "green",
      durMs: HOP_MS,
      beginMs: 0,
      stopMarker: event.blocked,
    } as PacketPulseData,
    selectable: false,
    focusable: false,
  });

  if (!event.blocked) {
    edges.push({
      id: `ping-pulse-${event.id}-1`,
      source: FIREWALL_NODE_ID,
      target: event.dst,
      type: "packet_pulse",
      data: {
        color: "green",
        durMs: HOP_MS,
        beginMs: HOP_MS,
        stopMarker: false,
      } as PacketPulseData,
      selectable: false,
      focusable: false,
    });
  }

  return edges;
}

// --- Component ---

interface Props {
  labReady: boolean;
  dropRules: ParsedRule[];
  pingEvent: PingEvent | null;
  onPingEventComplete: () => void;
}

export default function TopologyPane({
  labReady,
  dropRules,
  pingEvent,
  onPingEventComplete,
}: Props) {
  const [nodes, , onNodesChange] = useNodesState<Node<DeviceNodeData>>(INITIAL_NODES);

  const edges: Edge[] = useMemo(() => {
    if (!pingEvent) return STATIC_EDGES;
    return [...STATIC_EDGES, ...buildPingPulseEdges(pingEvent)];
  }, [pingEvent]);

  // Auto-dismiss ping animation after total duration.
  useEffect(() => {
    if (!pingEvent) return;
    const hops = pingEvent.blocked ? 1 : 2;
    const total = HOP_MS * hops + 400; // leave time for the red-X to be visible
    const timer = setTimeout(onPingEventComplete, total);
    return () => clearTimeout(timer);
  }, [pingEvent, onPingEventComplete]);

  return (
    <LabReadyContext.Provider value={labReady}>
      <DropRulesContext.Provider value={dropRules}>
        <div className="h-full w-full">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
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
