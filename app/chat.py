import ipaddress
import json
import os
import re
import socket

import httpx

from app.lab.models import Scenario

# Overridable for remote Ollama (vast.ai). Default = local daemon; an SSH
# tunnel (-L 11434:localhost:11434) also lands on the default.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")

# Explicit context size. Ollama's default num_ctx varies by version/host and
# it truncates an over-long prompt SILENTLY from the top — which eats the
# system prompt and tool definitions first, after which any model stops tool
# calling. 16384 covers long demo chats and L=8 experiment sequences; KV-cache
# cost fits the 64 GB GPU tier alongside a 70B Q4.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))

MAX_TOOL_ITERATIONS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "block_traffic",
            "description": "Block traffic between two nodes. Adds a firewall DROP rule. For a specific service set proto=tcp|udp and port. Known services in this lab: HTTP/web = proto tcp, port 80; HTTPS = proto tcp, port 443; PostgreSQL/database = proto tcp, port 5432; the UDP echo service = proto udp, port 9999. Use proto=icmp (no port) for ping. dst may also be a PUBLIC INTERNET hostname (e.g. example.com) — the rule then blocks that website's resolved IP addresses on the source's firewall.",
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
            "description": "Allow traffic between two nodes. Adds a firewall ACCEPT rule. dst may also be a public internet hostname (e.g. example.com) to re-enable access to a previously blocked website.",
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
            "name": "run_command",
            "description": "Run a shell command INSIDE a lab PC's container (real docker exec) and return its stdout/stderr/exit code. Use this to inspect or FIX a PC — for example to upgrade an outdated, vulnerable package after a vulnerability_scan flags it. Only PC nodes are allowed (not firewalls, routers, or switches). Every command is shown to the user. To fix an outdated Alpine package (e.g. OpenSSL 1.1.1 on pc1a), point apk at a current branch and upgrade: sh -c 'sed -i \"s|/v3.14/|/v3.20/|g\" /etc/apk/repositories && apk update && apk upgrade --no-cache --available'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "The PC node id to run inside (e.g. pc1a)."},
                    "command": {"type": "string", "description": "The shell command to run, e.g. \"openssl version\" or a full 'sh -c ...' upgrade line."},
                },
                "required": ["node", "command"],
            },
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


TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def salvage_content_tool_calls(content: str) -> tuple[list[dict], str]:
    """Recover tool calls a model wrote as JSON TEXT instead of emitting them
    through the tool-calling mechanism (observed with qwen2.5: once one reply
    contains a ```json example, the model imitates it forever and 'actions'
    become prose claims). Scans the content for JSON objects shaped like
    {"name": <known tool>, "arguments": {...}}, returns them in Ollama
    tool_call shape plus the content with those blocks removed — so the claim
    becomes the action instead of a lie.
    """
    if not content or "{" not in content:
        return [], content
    decoder = json.JSONDecoder()
    calls: list[dict] = []
    spans: list[tuple[int, int]] = []
    seen: set[str] = set()
    i = 0
    while True:
        start = content.find("{", i)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(content[start:])
        except ValueError:
            i = start + 1
            continue
        if (
            isinstance(obj, dict)
            and obj.get("name") in TOOL_NAMES
            and isinstance(obj.get("arguments", {}), dict)
            # JSON the model introduces as an EXAMPLE is illustration, not
            # intent — executing it would mutate the network on an
            # informational turn (observed live: a "what can you block" answer
            # got its example blocks applied). Only the text BEFORE the JSON
            # counts: trailing "for example, verify with curl…" prose after an
            # intended call must not veto the salvage.
            and not re.search(
                r"\bexamples?\b|\bfor instance\b|\be\.g\.|\bsuch as\b",
                content[max(0, start - 200):start],
                re.I,
            )
        ):
            key = json.dumps(obj, sort_keys=True)
            if key not in seen:  # model often repeats the same block
                seen.add(key)
                calls.append({"function": {"name": obj["name"], "arguments": obj.get("arguments", {})}})
            spans.append((start, start + end))
        i = start + max(end, 1)
    if not calls:
        return [], content
    cleaned = content
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    # drop the now-empty code fences the JSON lived in
    cleaned = re.sub(r"```(?:json)?\s*```", "", cleaned).strip()
    return calls, cleaned


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
    # Caller options layer on top of the explicit context size.
    payload["options"] = {"num_ctx": OLLAMA_NUM_CTX, **(options or {})}
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


# Public-internet targets the user has blocked/allowed this session:
# resolved IP -> hostname, so rule listings and the system prompt can label a
# raw public IP with the website name it stands for.
EXTERNAL_IP_NAMES: dict[str, str] = {}

# A dst that is not a lab node may be a public website: a dotted hostname or a
# literal IPv4. Anything else stays an "unknown node" error.
_EXTERNAL_HOST_RE = re.compile(
    r"^(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+)$",
    re.I,
)


def resolve_external(host: str, max_ips: int = 4) -> list[str]:
    """Resolve a public hostname to its IPv4 addresses (the firewall rules
    block IPs, not names). Resolution runs on the backend host; the lab
    containers use the same upstream DNS via docker, so for stable sites
    (example.com) both see identical addresses. Raises RuntimeError with a
    plain message when the name does not resolve — dispatch turns that into a
    tool error the model can relay."""
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET,
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise RuntimeError(f"Could not resolve '{host}': {e}") from e
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    ips = ips[:max_ips]
    for ip in ips:
        EXTERNAL_IP_NAMES[ip] = host
    return ips


def alias_map(scenario: Scenario) -> dict[str, str]:
    """Scenario-declared hostname aliases -> node id, lowercased (e.g.
    'example.com' -> 'pc1b'). Same names the hosts pass writes into /etc/hosts."""
    result: dict[str, str] = {}
    for node in scenario.nodes:
        for alias in node.aliases:
            result[alias.lower()] = node.id
    return result


# ── Vulnerable-node detection (the demo's "one vulnerable PC" — point 12) ──
# A node is flagged vulnerable when the OpenSSL it actually runs is the EOL 1.x
# series (weblab-vuln = Alpine 3.14 / OpenSSL 1.1.1, which genuinely carries
# CVE-2024-5535 etc.). This is read live from the RUNNING container, so an
# in-container `apk upgrade` (via run_command) that moves OpenSSL to 3.x clears
# the flag on the next check — nothing simulated. Only PCs are checked, and
# only the cost of one `openssl version` exec per PC.
_OPENSSL_VER_RE = re.compile(r"OpenSSL\s+(\d+)\.(\d+)\.(\d+)", re.I)
_ALPINE_FIX_CMD = (
    "sh -c 'sed -i \"s|/v3.14/|/v3.20/|g\" /etc/apk/repositories "
    "&& apk update && apk upgrade --no-cache --available'"
)
# Only images we deliberately ship with an old package are watched — so the
# vulnerability check runs at most ONE container exec (pc1a), never a probe of
# every PC (a slow/unhealthy container would otherwise stall the GUI's
# /vulnerable poll for up to docker_exec's timeout per node).
_WATCHED_IMAGE_MARKER = "weblab-vuln"


def node_vuln_status(scenario: Scenario, topology, node_id: str) -> dict | None:
    """Live vulnerability status for one WATCHED PC, or None if the node is not
    a watched-image PC / the probe fails. Keys: vulnerable, package, installed,
    severity, cve_example, fix_hint."""
    node = next((n for n in scenario.nodes if n.id == node_id), None)
    if node is None or node.role != "pc":
        return None
    if _WATCHED_IMAGE_MARKER not in (node.image or ""):
        return None  # not a deliberately-vulnerable host — skip the exec entirely
    try:
        r = topology.exec(scenario.name, node_id, ["openssl", "version"])
    except Exception:
        return None
    m = _OPENSSL_VER_RE.search((r.get("stdout") or "") + (r.get("stderr") or ""))
    if not m:
        return None  # no openssl binary (e.g. a postgres PC) — not our watched host
    major = int(m.group(1))
    installed = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    vulnerable = major < 3  # OpenSSL 1.x/2.x are EOL
    status = {
        "node": node_id,
        "vulnerable": vulnerable,
        "package": "openssl",
        "installed": installed,
    }
    if vulnerable:
        status.update({
            "severity": "CRITICAL",
            "cve_example": "CVE-2024-5535",
            "fix_hint": f"run_command on {node_id}: {_ALPINE_FIX_CMD}",
        })
    return status


# Nodes the user has actually run a scan on this lab session. A node is only
# painted "(vulnerable)" in the GUI AFTER it has been scanned — so the topology
# looks normal until the user asks for a scan, then the flagged node turns red,
# and it returns to normal once the LLM fixes it. Cleared on lab start/stop/reset.
SCANNED_NODES: set[str] = set()


def vulnerable_nodes(scenario: Scenario, topology) -> list[str]:
    """Node ids currently flagged vulnerable — the GUI reads this to paint them
    red. Only nodes that have BEEN SCANNED this session are considered, so
    nothing is red until the user runs a scan (and a fixed node clears). Costs
    at most one container exec (only watched-image scanned nodes are probed)."""
    out: list[str] = []
    for nid in SCANNED_NODES:
        st = node_vuln_status(scenario, topology, nid)
        if st and st.get("vulnerable"):
            out.append(nid)
    return out


def validate_node_args(args: dict, scenario: Scenario) -> str | None:
    """Validate src/dst (and run_command's `node`) against the scenario — and
    canonicalize aliases IN PLACE (a user says "block example.com"; the model
    passes it through) so every downstream consumer sees the real node id,
    deterministically.

    `dst` may additionally be a PUBLIC INTERNET hostname or IP (the lab has a
    real, firewalled internet uplink): it is normalized in place (scheme/path/
    port stripped, lowercased) and left for dispatch_tool to resolve. `src`
    must always be a lab node — traffic originates inside the lab."""
    node_ids = {n.id for n in scenario.nodes}
    # run_command targets a single PC via `node`; validate it like a lab node.
    if "node" in args and "src" not in args and "dst" not in args:
        val = str(args.get("node", "")).strip()
        if val not in node_ids:
            return f"Unknown node '{args.get('node')}'. Valid nodes: {sorted(node_ids)}"
        return None
    aliases = alias_map(scenario)
    for field in ("src", "dst"):
        val = args.get(field)
        if not val:
            continue
        v = str(val).strip()
        if field == "dst":
            # Models pass websites as URLs ("http://example.com/") — keep the
            # bare host.
            v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v, flags=re.I)
            v = v.split("/")[0].split(":")[0]
        if v in node_ids:
            args[field] = v
            continue
        canonical = aliases.get(v.lower())
        if canonical:
            args[field] = canonical
            continue
        if field == "dst" and _EXTERNAL_HOST_RE.match(v):
            args[field] = v.lower()
            continue
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
        proto = args.get("proto", "icmp")
        port = args.get("port")
        fw_id = args.get("firewall") or resolve_firewall(src_ip, scenario)
        dst = args["dst"]
        if dst in ip_map:
            # Lab pair — unchanged single-rule path (experiment runner relies
            # on this shape).
            return security.block(src_ip, ip_map[dst], proto, port, fw_id=fw_id)
        # Public website: one DROP per resolved IP, on the source-side firewall
        # (internet-bound traffic crosses only the local fw before NAT).
        dst_ips = resolve_external(dst)
        results = [security.block(src_ip, d, proto, port, fw_id=fw_id) for d in dst_ips]
        return {"blocked_website": dst, "resolved_ips": dst_ips,
                "firewall": fw_id, "results": results}

    elif name == "allow_traffic":
        src_ip = ip_map.get(args["src"])
        proto = args.get("proto", "icmp")
        fw_id = args.get("firewall") or resolve_firewall(src_ip, scenario)
        dst = args["dst"]
        if dst in ip_map:
            return security.allow(src_ip, ip_map[dst], proto, fw_id=fw_id)
        dst_ips = resolve_external(dst)
        results = [security.allow(src_ip, d, proto, fw_id=fw_id) for d in dst_ips]
        return {"allowed_website": dst, "resolved_ips": dst_ips,
                "firewall": fw_id, "results": results}

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

    elif name == "run_command":
        # Real shell INTO a PC container (docker exec). PCs only — a bad command
        # on a firewall/router could break routing or firewalld mid-demo.
        node_id = args["node"]
        node = next((n for n in scenario.nodes if n.id == node_id), None)
        if node is None:
            return {"error": f"Unknown node '{node_id}'."}
        if node.role != "pc":
            pcs = sorted(n.id for n in scenario.nodes if n.role == "pc")
            return {"error": f"run_command only runs on PCs (not a {node.role}). Valid: {pcs}"}
        command = str(args.get("command", "")).strip()
        if not command:
            return {"error": "No command given."}
        # Run through a shell so pipelines / && / sed quoting work as written.
        result = topology.exec(scenario.name, node_id, ["sh", "-c", command])
        out = {
            "node": node_id,
            "command": command,
            "exit": result.get("exit"),
            "stdout": (result.get("stdout") or "")[-4000:],
            "stderr": (result.get("stderr") or "")[-2000:],
        }
        # Surface the resulting vulnerability status so the model can confirm a
        # fix worked (and the acceptance guard sees a real state change).
        st = node_vuln_status(scenario, topology, node_id)
        if st is not None:
            out["vuln_status"] = st
        return out

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
        # Remember which nodes have been scanned, so the GUI only flags a node
        # "(vulnerable)" after the user actually scans it (not on lab start).
        SCANNED_NODES.update(node_ids)
        # Per-node live vulnerability status (the "one vulnerable PC" story):
        # drives the red "(vulnerable)" label in the GUI, and tells the model
        # exactly which node to fix and how.
        node_status = [
            st for nid in node_ids
            if (st := node_vuln_status(scenario, topology, nid)) is not None
        ]
        if node_status:
            result["node_status"] = node_status
            result["vulnerable_nodes"] = [s["node"] for s in node_status if s["vulnerable"]]
        return result

    return {"error": f"Unknown tool: {name}"}
