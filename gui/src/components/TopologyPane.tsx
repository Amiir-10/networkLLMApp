import { useMemo } from "react";
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

// --- Custom Node ---

const handleStyle = { background: "transparent", border: "none", width: 1, height: 1 };

function DeviceNode({ data }: NodeProps) {
  const { label, ip, role } = data as { label: string; ip: string; role: string };
  return (
    <div className="flex flex-col items-center gap-1 cursor-grab active:cursor-grabbing relative">
      <Handle type="target" position={Position.Left} style={handleStyle} />
      {role === "firewall" ? <ShieldIcon /> : <PcIcon />}
      <span className="text-xs font-semibold text-gray-700">{label}</span>
      <span className="text-[10px] text-gray-400 font-mono">{ip}</span>
      <Handle type="source" position={Position.Right} style={handleStyle} />
    </div>
  );
}

// --- Custom Edge ---

function AnimatedDashedEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  label,
  data,
}: EdgeProps) {
  const status = (data?.status as string) || "inactive";

  const edgePath = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`;

  const midX = (sourceX + targetX) / 2;
  const midY = (sourceY + targetY) / 2;

  let strokeColor = "#cbd5e1";
  let dashArray = "6 4";
  let opacity = 1;
  let animationClass = "";

  if (status === "active") {
    strokeColor = "#22c55e";
    dashArray = "8 4";
    animationClass = "edge-flow";
  } else if (status === "blocked") {
    strokeColor = "#d1d5db";
    dashArray = "3 6";
    opacity = 0.4;
  }

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
        className={animationClass}
      />
      {label && (
        <text
          x={midX}
          y={midY - 8}
          textAnchor="middle"
          className="text-[9px] fill-gray-400"
          style={{ fontSize: 9 }}
        >
          {String(label)}
        </text>
      )}
    </>
  );
}

// --- Node/Edge types ---

const nodeTypes = { device: DeviceNode };
const edgeTypes = { animated_dashed: AnimatedDashedEdge };

// --- Component ---

export type ConnectionStatus = "inactive" | "active" | "blocked";

interface Props {
  connectionStatus: ConnectionStatus;
}

const initialNodes: Node[] = [
  {
    id: "pc1",
    type: "device",
    position: { x: 40, y: 80 },
    data: { label: "pc1", ip: "10.99.0.10", role: "pc" },
  },
  {
    id: "fw",
    type: "device",
    position: { x: 200, y: 70 },
    data: { label: "fw", ip: "firewalld", role: "firewall" },
  },
  {
    id: "pc2",
    type: "device",
    position: { x: 360, y: 80 },
    data: { label: "pc2", ip: "10.99.1.10", role: "pc" },
  },
];

function buildEdges(status: ConnectionStatus): Edge[] {
  return [
    {
      id: "pc1-fw",
      source: "pc1",
      target: "fw",
      type: "animated_dashed",
      label: "10.99.0.0/24",
      data: { status },
    },
    {
      id: "fw-pc2",
      source: "fw",
      target: "pc2",
      type: "animated_dashed",
      label: "10.99.1.0/24",
      data: { status },
    },
  ];
}

export default function TopologyPane({ connectionStatus }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const edges = useMemo(() => buildEdges(connectionStatus), [connectionStatus]);

  return (
    <div className="h-full w-full">
      <style>{`
        @keyframes dash-flow {
          to { stroke-dashoffset: -24; }
        }
        .edge-flow {
          animation: dash-flow 1.5s linear infinite;
        }
      `}</style>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.4 }}
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
  );
}
