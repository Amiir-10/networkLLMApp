// Data-driven topology builder.
//
// Given a scenario graph from the backend (GET /scenarios/{name}), derive
// everything the topology view needs — React Flow device nodes (auto-laid-out
// with dagre), subnet "cloud" background nodes, wire edges, an IP->node map,
// and a link-graph BFS for the ping animation. Nothing here is hardcoded to a
// specific scenario, so central-hub and two-subnet-ixp (and any future YAML)
// all render from the same code.

import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";
import type { ScenarioGraph, GraphNode } from "./api";

export type NodeRole = "pc" | "router" | "firewall" | "switch";

export interface DeviceNodeData {
  label: string;
  ip: string;        // primary IP for display ("" for L2 switches)
  role: NodeRole;
  ports: number[];
  [key: string]: unknown;
}

export interface CloudNodeData {
  cidr: string;
  label: string;   // human label, e.g. "LAN · 10.10.1.0/24" / "IXP · 100.64.0.0/24"
  [key: string]: unknown;
}

export interface BuiltTopology {
  deviceNodes: Node<DeviceNodeData>[];
  cloudNodes: Node<CloudNodeData>[];
  edges: Edge[];
  ipToNodeId: Record<string, string>;
  firewallIds: string[];
  /** Edge id connecting two directly-linked nodes, or null. */
  wireIdForHop: (a: string, b: string) => string | null;
  /** Ordered edge ids a packet src->dst traverses (BFS shortest path). */
  pathWires: (src: string, dst: string) => string[];
  /** Node ids on the src->dst path, in order. */
  pathNodes: (src: string, dst: string) => string[];
}

const NODE_W = 150;
const NODE_H = 96;
const CLOUD_PAD = 32;
const CLOUD_LABEL_H = 20;
const MIN_CLOUD_MEMBERS = 3;

// ── IPv4 helpers ───────────────────────────────────────────────────────────
function ipToInt(ip: string): number {
  return ip.split(".").reduce((acc, o) => (acc << 8) + (parseInt(o, 10) & 255), 0) >>> 0;
}

/** "10.10.1.11/24" -> "10.10.1.0/24" (network address). */
function networkOf(cidr: string): string {
  const [ip, maskStr] = cidr.split("/");
  const mask = parseInt(maskStr, 10);
  const maskBits = mask === 0 ? 0 : (0xffffffff << (32 - mask)) >>> 0;
  const net = (ipToInt(ip) & maskBits) >>> 0;
  const octets = [net >>> 24, (net >>> 16) & 255, (net >>> 8) & 255, net & 255];
  return `${octets.join(".")}/${mask}`;
}

const undirectedKey = (a: string, b: string) => [a, b].sort().join("|");
const edgeId = (a: string, b: string) => [a, b].sort().join("__");

export function buildTopology(graph: ScenarioGraph): BuiltTopology {
  const nodes = graph.nodes;
  const byId: Record<string, GraphNode> = Object.fromEntries(nodes.map((n) => [n.id, n]));

  // ── IP -> node id (host IPs only) + primary display IP per node ──────────
  const ipToNodeId: Record<string, string> = {};
  const primaryIp: Record<string, string> = {};
  for (const n of nodes) {
    for (const iface of n.interfaces) {
      if (!iface.ip) continue;
      const host = iface.ip.split("/")[0];
      ipToNodeId[host] = n.id;
      if (!(n.id in primaryIp)) primaryIp[n.id] = host;
    }
  }

  // ── Undirected links (deduped) + adjacency ───────────────────────────────
  const linkSet = new Map<string, [string, string]>();
  const adjacency: Record<string, Set<string>> = {};
  for (const n of nodes) {
    adjacency[n.id] ??= new Set();
    for (const iface of n.interfaces) {
      if (!byId[iface.to]) continue;
      linkSet.set(undirectedKey(n.id, iface.to), [n.id, iface.to]);
      (adjacency[n.id] ??= new Set()).add(iface.to);
      (adjacency[iface.to] ??= new Set()).add(n.id);
    }
  }
  const links = [...linkSet.values()];

  // ── Subnet membership (clouds) ────────────────────────────────────────────
  // L3: a node is in a subnet if it has an interface with an IP in that net.
  // L2: a switch joins the subnet shared by its IP-bearing neighbours.
  const subnets = new Map<string, Set<string>>();
  const add = (net: string, id: string) => (subnets.get(net) ?? subnets.set(net, new Set()).get(net)!).add(id);
  for (const n of nodes) {
    for (const iface of n.interfaces) {
      if (iface.ip) add(networkOf(iface.ip), n.id);
    }
  }
  for (const n of nodes) {
    if (n.role !== "switch") continue;
    for (const iface of n.interfaces) {
      const peer = byId[iface.to];
      const back = peer?.interfaces.find((i) => i.to === n.id && i.ip);
      if (back?.ip) add(networkOf(back.ip), n.id);
    }
  }

  // ── Layout (dagre) ────────────────────────────────────────────────────────
  // Dagre ranks flow source → target, and edge direction here comes from YAML
  // interface listing order — arbitrary. With a SINGLE gateway firewall the
  // topology is a hierarchy (WAN → firewall → LANs), so orient edges by role
  // rank and lay it out left-to-right: router — firewall — hosts (central-hub
  // previously drew the firewall leftmost with the router stacked among the
  // PCs). Multi-firewall topologies (two-subnet-ixp: two peer sites around an
  // IXP) have no such hierarchy — role orientation would unmirror the two
  // sites, so they keep the natural edge order, but lay out TOP-TO-BOTTOM:
  // the ranks (site A hosts / site A edge / IXP / site B edge / site B hosts)
  // then stack vertically, which matches the tall topology pane and renders
  // far larger under fitView than the previous ultra-wide LR strip.
  const ROLE_RANK: Record<string, number> = { router: 0, firewall: 1, switch: 2, pc: 3 };
  const singleFirewall = nodes.filter((n) => n.role === "firewall").length === 1;
  const g = new dagre.graphlib.Graph();
  g.setGraph(
    singleFirewall
      ? { rankdir: "LR", nodesep: 44, ranksep: 110, marginx: 40, marginy: 40 }
      : { rankdir: "TB", nodesep: 52, ranksep: 32, marginx: 24, marginy: 24 }
  );
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  // Edges that cross a cloud boundary (endpoints belong to different drawn
  // subnets) get an extra rank of separation, so adjacent cloud rectangles
  // (LAN ↔ IXP in two-subnet-ixp) don't overlap each other or each other's
  // labels — ranksep alone is smaller than two cloud paddings.
  const cloudsOf = (id: string) =>
    [...subnets].filter(([, m]) => m.size >= MIN_CLOUD_MEMBERS && m.has(id))
      .map(([cidr]) => cidr).sort().join(",");
  for (const [a, b] of links) {
    const minlen = cloudsOf(a) !== cloudsOf(b) ? 3 : 1;
    const flip =
      singleFirewall && (ROLE_RANK[byId[b].role] ?? 9) < (ROLE_RANK[byId[a].role] ?? 9);
    if (flip) g.setEdge(b, a, { minlen });
    else g.setEdge(a, b, { minlen });
  }
  dagre.layout(g);

  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const p = g.node(n.id); // dagre gives center coords
    pos[n.id] = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
  }

  // ── Device nodes ──────────────────────────────────────────────────────────
  const deviceNodes: Node<DeviceNodeData>[] = nodes.map((n) => ({
    id: n.id,
    type: "device",
    position: pos[n.id],
    zIndex: 1,
    data: {
      label: n.id,
      ip: n.role === "switch" ? "" : primaryIp[n.id] ?? "",
      role: n.role as NodeRole,
      ports: n.ports ?? [],
    },
  }));

  // ── Cloud nodes (one per subnet; bbox of members, padded) ─────────────────
  // A cloud represents a subnet, not a device. Only draw it where devices
  // genuinely SHARE a subnet (>= MIN_CLOUD_MEMBERS): the LAN segments and the
  // IXP fabric. Point-to-point links (a /30 transit, or a host alone on its own
  // /24 with just its gateway) get no cloud — otherwise the gateway, which sits
  // on every subnet, would be wrapped in a bubble per neighbour ("a cloud per
  // device"). E.g. central-hub's PCs are each on their own /24, so it gets no
  // clouds; two-subnet-ixp gets LAN-A, LAN-B and the IXP.
  const cloudNodes: Node<CloudNodeData>[] = [];
  for (const [cidr, members] of subnets) {
    const ids = [...members].filter((id) => pos[id]);
    if (ids.length < MIN_CLOUD_MEMBERS) continue;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of ids) {
      minX = Math.min(minX, pos[id].x);
      minY = Math.min(minY, pos[id].y);
      maxX = Math.max(maxX, pos[id].x + NODE_W);
      maxY = Math.max(maxY, pos[id].y + NODE_H);
    }
    // Name the subnet by what lives on it so the clouds read as distinct
    // segments (not just "/24"): a subnet with hosts = LAN; one that only joins
    // routers = an IXP/peering fabric.
    const roles = ids.map((id) => byId[id]?.role);
    const kind = roles.includes("pc")
      ? "LAN"
      : roles.filter((r) => r === "router").length >= 2
      ? "IXP"
      : "subnet";
    cloudNodes.push({
      id: `cloud:${cidr}`,
      type: "cloud",
      position: { x: minX - CLOUD_PAD, y: minY - CLOUD_PAD - CLOUD_LABEL_H },
      zIndex: 0,
      selectable: false,
      draggable: false,
      data: { cidr, label: `${kind} · ${cidr}` },
      style: {
        width: maxX - minX + CLOUD_PAD * 2,
        height: maxY - minY + CLOUD_PAD * 2 + CLOUD_LABEL_H,
      },
    });
  }

  // ── Edges (handles chosen from post-layout relative direction) ────────────
  const edges: Edge[] = links.map(([a, b]) => {
    const dx = pos[b].x - pos[a].x;
    const dy = pos[b].y - pos[a].y;
    let sourceHandle: string, targetHandle: string;
    if (Math.abs(dx) >= Math.abs(dy)) {
      sourceHandle = dx >= 0 ? "rs" : "ls";
      targetHandle = dx >= 0 ? "l" : "r";
    } else {
      sourceHandle = dy >= 0 ? "bs" : "ts";
      targetHandle = dy >= 0 ? "t" : "b";
    }
    return { id: edgeId(a, b), source: a, sourceHandle, target: b, targetHandle, type: "wire", zIndex: 2 };
  });

  // ── BFS shortest path for the ping animation ──────────────────────────────
  function pathNodes(src: string, dst: string): string[] {
    if (src === dst || !adjacency[src] || !adjacency[dst]) return [];
    const prev: Record<string, string | null> = { [src]: null };
    const queue = [src];
    while (queue.length) {
      const cur = queue.shift()!;
      if (cur === dst) break;
      for (const nb of adjacency[cur] ?? []) {
        if (!(nb in prev)) { prev[nb] = cur; queue.push(nb); }
      }
    }
    if (!(dst in prev)) return [];
    const path: string[] = [];
    let at: string | null = dst;
    while (at !== null) { path.unshift(at); at = prev[at]; }
    return path;
  }

  const wireForHop = (a: string, b: string): string | null =>
    linkSet.has(undirectedKey(a, b)) ? edgeId(a, b) : null;

  function pathWires(src: string, dst: string): string[] {
    const p = pathNodes(src, dst);
    const wires: string[] = [];
    for (let i = 0; i < p.length - 1; i++) {
      const w = wireForHop(p[i], p[i + 1]);
      if (w) wires.push(w);
    }
    return wires;
  }

  return {
    deviceNodes,
    cloudNodes,
    edges,
    ipToNodeId,
    firewallIds: nodes.filter((n) => n.role === "firewall").map((n) => n.id),
    wireIdForHop: wireForHop,
    pathWires,
    pathNodes,
  };
}

export function ipToNodeLabel(map: Record<string, string>, ip: string | null | undefined): string {
  if (!ip) return "any";
  return map[ip] ?? ip;
}
