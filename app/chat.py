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
]

def _node_ip_map(scenario: Scenario) -> dict[str, str]:
    result = {}
    for node in scenario.nodes:
        for iface in node.interfaces:
            ip = iface.ip.split("/")[0]
            result[node.id] = ip
            break
    return result


def call_ollama(model: str, messages: list[dict]) -> dict:
    with httpx.Client(timeout=60.0) as client:
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
    firewall_driver,
    lab_driver,
) -> dict:
    ip_map = _node_ip_map(scenario)

    if name == "block_traffic":
        src_ip = ip_map.get(args["src"])
        dst_ip = ip_map.get(args["dst"])
        proto = args.get("proto", "icmp")
        return firewall_driver.block(src_ip, dst_ip, proto)

    elif name == "allow_traffic":
        src_ip = ip_map.get(args["src"])
        dst_ip = ip_map.get(args["dst"])
        proto = args.get("proto", "icmp")
        return firewall_driver.allow(src_ip, dst_ip, proto)

    elif name == "flush_rules":
        return firewall_driver.flush()

    elif name == "list_rules":
        return firewall_driver.list_rules()

    elif name == "ping_test":
        src = args["src"]
        dst = args["dst"]
        dst_ip = ip_map.get(dst, dst)
        result = lab_driver.exec(scenario.name, src, ["ping", "-c", "2", "-W", "2", dst_ip])
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
        rules = firewall_driver.list_rules()
        return {"topology": nodes_info, "firewall_rules": rules}

    return {"error": f"Unknown tool: {name}"}
