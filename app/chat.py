import ipaddress
import json
import os
import re

import httpx

from app.lab.models import Scenario

# Overridable for remote Ollama (vast.ai). Default = local daemon; an SSH
# tunnel (-L 11434:localhost:11434) also lands on the default.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")

MAX_TOOL_ITERATIONS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "block_traffic",
            "description": "Block traffic between two nodes. Adds a firewall DROP rule. For a specific service set proto=tcp|udp and port. Known services in this lab: HTTP/web = proto tcp, port 80; HTTPS = proto tcp, port 443; PostgreSQL/database = proto tcp, port 5432; the UDP echo service = proto udp, port 9999. Use proto=icmp (no port) for ping.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source node ID (e.g. pc1)"},
                    "dst": {"type": "string", "description": "Destination node ID (e.g. pc2)"},
                    "proto": {"type": "string", "enum": ["icmp", "tcp", "udp", "all"], "default": "icmp"},
                    "port": {"type": "integer", "description": "Port for a tcp/udp service rule (e.g. 80, 5432). Omit for icmp or to block the whole protocol."},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allow_traffic",
            "description": "Allow traffic between two nodes. Adds a firewall ACCEPT rule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source node ID"},
                    "dst": {"type": "string", "description": "Destination node ID"},
                    "proto": {"type": "string", "enum": ["icmp", "tcp", "udp", "all"], "default": "icmp"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flush_rules",
            "description": "Remove all firewall rules, restoring default allow-all policy.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rules",
            "description": "List all currently active firewall rules.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ping_test",
            "description": "Run a ping test from one node to another to check connectivity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source node ID to ping from"},
                    "dst": {"type": "string", "description": "Destination node ID to ping to"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_state",
            "description": "Describe the current network topology, node IPs, and firewall status. Use this when the user asks about the network state without wanting to change anything.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vulnerability_scan",
            "description": "Run a security vulnerability scan on one or more nodes' container images. Reports CVEs, CIS Docker benchmark issues, hardcoded secrets, and supply-chain risks. Use when the user asks to scan, audit, or check the security/vulnerabilities of a node. The target is a single node id (e.g. 'pc1'), several node ids separated by commas/spaces (e.g. 'pc1, pc2, fw'), or the word 'all' to scan every node. Nodes sharing the same image are scanned only once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "A node id (e.g. 'pc1'), a comma/space-separated list of node ids (e.g. 'pc1, fw'), or 'all' for every node."},
                },
                "required": ["target"],
            },
        },
    },
]


def resolve_firewall(src_ip: str | None, scenario: Scenario) -> str | None:
    """Pick which firewall enforces a rule — deterministically, never via the
    model (llama3.1:8b won't reliably choose; see vault feedback). The firewall
    whose interface subnet contains src_ip; else the first firewall; else None
    (no firewall in the scenario). For a single-firewall scenario this always
    returns that firewall, so the resolution is a no-op there."""
    fw_nodes = [n for n in scenario.nodes if n.role == "firewall"]
    if not fw_nodes:
        return None
    if src_ip:
        try:
            ip = ipaddress.ip_address(src_ip)
            for n in fw_nodes:
                for iface in n.interfaces:
                    if iface.ip and ip in ipaddress.ip_network(iface.ip, strict=False):
                        return n.id
        except ValueError:
            pass
    return fw_nodes[0].id


def _node_image_map(scenario: Scenario) -> dict[str, str]:
    return {node.id: node.image for node in scenario.nodes}

def _node_ip_map(scenario: Scenario) -> dict[str, str]:
    result = {}
    for node in scenario.nodes:
        for iface in node.interfaces:
            if not iface.ip:
                continue  # switch ports are L2-only; switches get no map entry
            result[node.id] = iface.ip.split("/")[0]
            break
    return result


def call_ollama(model: str, messages: list[dict], options: dict | None = None) -> dict:
    # 300s: on CPU Ollama a single tool-round has been measured at ~120s
    # (2026-07-11, warm model, leftover containers on the box) — the old 120s
    # cap sat exactly on that line, so a cold load or a busy machine tripped
    # it and the GUI surfaced an opaque error. The experiment runner records
    # per-step wall time itself, so a generous cap costs nothing.
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
    }
    # Ollama sampling options (temperature, seed, ...). The experiment runner
    # fixes temperature=0 so repetitions measure the model, not the sampler.
    if options:
        payload["options"] = options
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


def validate_node_args(args: dict, scenario: Scenario) -> str | None:
    node_ids = {n.id for n in scenario.nodes}
    for field in ("src", "dst"):
        val = args.get(field)
        if val and val not in node_ids:
            return f"Unknown node '{val}'. Valid nodes: {sorted(node_ids)}"
    return None


def dispatch_tool(
    name: str,
    args: dict,
    scenario: Scenario,
    security,
    topology,
) -> dict:
    ip_map = _node_ip_map(scenario)

    if name == "block_traffic":
        src_ip = ip_map.get(args["src"])
        dst_ip = ip_map.get(args["dst"])
        proto = args.get("proto", "icmp")
        port = args.get("port")
        fw_id = args.get("firewall") or resolve_firewall(src_ip, scenario)
        return security.block(src_ip, dst_ip, proto, port, fw_id=fw_id)

    elif name == "allow_traffic":
        src_ip = ip_map.get(args["src"])
        dst_ip = ip_map.get(args["dst"])
        proto = args.get("proto", "icmp")
        fw_id = args.get("firewall") or resolve_firewall(src_ip, scenario)
        return security.allow(src_ip, dst_ip, proto, fw_id=fw_id)

    elif name == "flush_rules":
        return security.flush()

    elif name == "list_rules":
        return security.list_rules()

    elif name == "ping_test":
        src = args["src"]
        dst = args["dst"]
        dst_ip = ip_map.get(dst, dst)
        result = topology.exec(scenario.name, src, ["ping", "-c", "2", "-W", "2", dst_ip])
        loss = "unknown"
        for line in result.get("stdout", "").splitlines():
            if "packet loss" in line:
                loss = line.strip()
        return {"ping_from": src, "ping_to": dst, "loss_line": loss, "raw": result}

    elif name == "describe_state":
        nodes_info = []
        for n in scenario.nodes:
            ips = [f"{iface.ip or 'L2'} -> {iface.to}" for iface in n.interfaces]
            nodes_info.append({"id": n.id, "role": n.role, "interfaces": ips})
        rules = security.list_rules()
        return {"topology": nodes_info, "firewall_rules": rules}

    elif name == "vulnerability_scan":
        # Parse the target string deterministically in the backend — llama3.1:8b
        # won't reliably emit a JSON array, so the tool schema stays a single
        # string and WE expand "all"/comma-separated into node ids here.
        target = str(args.get("target", "")).strip()
        image_map = _node_image_map(scenario)
        if target.lower() in ("all", "network", "everything", "*"):
            node_ids = list(image_map.keys())
        else:
            node_ids = [t for t in re.split(r"[,\s]+", target) if t]
        if not node_ids:
            return {"error": "No scan target given. Provide a node id, a list, or 'all'."}
        unknown = [n for n in node_ids if n not in image_map]
        if unknown:
            return {"error": f"Unknown node(s) {unknown}. Valid nodes: {sorted(image_map)}"}
        # Dedupe to unique images, recording which requested nodes share each.
        images: list[str] = []
        nodes_for_image: dict[str, list[str]] = {}
        for nid in node_ids:
            img = image_map[nid]
            if img not in nodes_for_image:
                nodes_for_image[img] = []
                images.append(img)
            nodes_for_image[img].append(nid)
        result = security.scan_images(images)
        for s in result["scans"]:
            s["nodes"] = nodes_for_image.get(s.get("image"), [])
        result["targets"] = node_ids
        return result

    return {"error": f"Unknown tool: {name}"}
