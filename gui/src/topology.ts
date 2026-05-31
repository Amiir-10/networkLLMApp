// Shared topology constants for the central-hub scenario.
// Source of truth for node positions, labels, and IP <-> node-id mapping
// used by both TopologyPane and rule-chip/animation rendering.

import type { Node } from "@xyflow/react";

export type NodeRole = "pc" | "router" | "firewall";

export interface DeviceNodeData {
  label: string;
  ip: string;
  role: NodeRole;
  [key: string]: unknown;
}

// Star layout: fw in the middle, pc1 top, pc2 right, pc3 bottom, router left (WAN-side).
export const INITIAL_NODES: Node<DeviceNodeData>[] = [
  {
    id: "pc1",
    type: "device",
    position: { x: 260, y: 40 },
    data: { label: "pc1", ip: "10.99.10.10", role: "pc" },
  },
  {
    id: "pc2",
    type: "device",
    position: { x: 460, y: 220 },
    data: { label: "pc2", ip: "10.99.20.10", role: "pc" },
  },
  {
    id: "pc3",
    type: "device",
    position: { x: 260, y: 400 },
    data: { label: "pc3", ip: "10.99.30.10", role: "pc" },
  },
  {
    id: "router",
    type: "device",
    position: { x: 60, y: 220 },
    data: { label: "router", ip: "203.0.113.2 (WAN)", role: "router" },
  },
  {
    id: "fw",
    type: "device",
    position: { x: 260, y: 220 },
    data: { label: "fw", ip: "firewalld", role: "firewall" },
  },
];

// IP (host part, no /mask) -> node id. Used to map parsed firewall rules
// (which carry IPs) back to display labels (which use node ids like "pc1").
export const IP_TO_NODE_ID: Record<string, string> = {
  "10.99.10.10": "pc1",
  "10.99.20.10": "pc2",
  "10.99.30.10": "pc3",
  "203.0.113.2": "router",
};

export const FIREWALL_NODE_ID = "fw";

export function ipToNodeLabel(ip: string | null | undefined): string {
  if (!ip) return "any";
  return IP_TO_NODE_ID[ip] ?? ip;
}

// Ordered-node-pair -> physical wire id. Wires are undirected, so both
// orderings of each link map to the same id. Lets a ping hop (a -> b) be
// translated to the existing edge to light up, instead of drawing a ghost edge.
// Wire ids must match the STATIC_EDGES declared in TopologyPane.
const PHYSICAL_LINKS: Array<[string, string, string]> = [
  ["pc1", "fw", "pc1-fw"],
  ["fw", "pc2", "fw-pc2"],
  ["fw", "pc3", "fw-pc3"],
  ["router", "fw", "router-fw"],
];

const hopKey = (a: string, b: string) => `${a}|${b}`;

export const WIRE_FOR_HOP: Record<string, string> = Object.fromEntries(
  PHYSICAL_LINKS.flatMap(([a, b, id]) => [
    [hopKey(a, b), id],
    [hopKey(b, a), id],
  ])
);

// Resolve the wire id connecting two adjacent nodes, or null if they are not
// directly linked (e.g. pc1 <-> pc2, which is two hops through fw).
export function wireIdForHop(a: string, b: string): string | null {
  return WIRE_FOR_HOP[hopKey(a, b)] ?? null;
}
