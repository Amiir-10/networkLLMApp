import json

import httpx

from app.lab.models import Scenario

OLLAMA_URL = "http://localhost:11434/api/chat"

MAX_TOOL_ITERATIONS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "block_traffic",
            "description": "Block traffic between two nodes. Adds a firewall DROP rule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source node ID (e.g. pc1)"},
                    "dst": {"type": "string", "description": "Destination node ID (e.g. pc2)"},
                    "proto": {"type": "string", "enum": ["icmp", "tcp", "udp", "all"], "default": "icmp"},
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
            "description": "Run a security vulnerability scan on a node's container image. Reports CVEs, CIS Docker benchmark issues, hardcoded secrets, and supply-chain risks. Use when the user asks to scan, audit, or check the security/vulnerabilities of a node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Node ID to scan (e.g. pc1, fw, router)"},
                },
                "required": ["target"],
            },
        },
    },
]


def _node_image_map(scenario: Scenario) -> dict[str, str]:
    return {node.id: node.image for node in scenario.nodes}

def _node_ip_map(scenario: Scenario) -> dict[str, str]:
    result = {}
    for node in scenario.nodes:
        for iface in node.interfaces:
            ip = iface.ip.split("/")[0]
            result[node.id] = ip
            break
    return result


def call_ollama(model: str, messages: list[dict]) -> dict:
    # 120s, not 60s: a cold model load (first call after the backend or Ollama
    # starts) can push the first response past 60s and trip the timeout even
    # though the request is fine. Subsequent calls are fast.
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
        })
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
        return security.block(src_ip, dst_ip, proto)

    elif name == "allow_traffic":
        src_ip = ip_map.get(args["src"])
        dst_ip = ip_map.get(args["dst"])
        proto = args.get("proto", "icmp")
        return security.allow(src_ip, dst_ip, proto)

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
            ips = [f"{iface.ip} -> {iface.to}" for iface in n.interfaces]
            nodes_info.append({"id": n.id, "role": n.role, "interfaces": ips})
        rules = security.list_rules()
        return {"topology": nodes_info, "firewall_rules": rules}

    elif name == "vulnerability_scan":
        target = args.get("target", "")
        image_map = _node_image_map(scenario)
        if target not in image_map:
            return {"error": f"Unknown node '{target}'. Valid nodes: {sorted(image_map)}"}
        return {"target": target, "image": image_map[target], **security.scan(image_map[target])}

    return {"error": f"Unknown tool: {name}"}
