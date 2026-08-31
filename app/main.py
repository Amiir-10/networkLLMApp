import asyncio
import base64
import fcntl
import hashlib
import json
import os
import pty
import re
import secrets
import struct
import subprocess
import termios
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin, quote, unquote

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.engines.topology import TopologyEngine, LabAlreadyRunning, LabNotFound
from app.engines.security import SecurityEngine
from app.lab.models import Scenario
from app.chat import call_ollama, validate_node_args, dispatch_tool, MAX_TOOL_ITERATIONS, _node_ip_map, resolve_firewall, OLLAMA_URL, OLLAMA_NUM_CTX, salvage_content_tool_calls, alias_map, EXTERNAL_IP_NAMES, vulnerable_nodes, SCANNED_NODES
from app.prompts import build_system_prompt
from app.metrics import MetricsCollector

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"
LAB_WORK_DIR = REPO_ROOT / "labs"

app = FastAPI(title="networkLLMApp", version="0.0.5")
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Showcase auth (opt-in) ────────────────────────────────────────────────
# Set SHOWCASE_PASSWORD to put the whole app (API + GUI + console WebSocket)
# behind HTTP Basic auth — used when hosting publicly for the supervisor.
# Unset (the default) = no auth, local dev unchanged. The username is ignored.
SHOWCASE_PASSWORD = os.environ.get("SHOWCASE_PASSWORD", "")
_AUTH_COOKIE = "showcase_token"


def _auth_token() -> str:
    # Derived, non-reversible cookie value. Browsers do not reliably attach
    # Basic credentials to WebSocket handshakes, but they DO send cookies —
    # this cookie is how the PTY console WebSocket stays protected.
    return hashlib.sha256(f"showcase:{SHOWCASE_PASSWORD}".encode()).hexdigest()[:32]


def _cookie_ok(cookies: dict[str, str]) -> bool:
    return secrets.compare_digest(cookies.get(_AUTH_COOKIE, ""), _auth_token())


# ── Abuse protection (supervisor request 2026-08-25, point 8) ─────────────
# Both mechanisms key on the ORIGINAL client IP. Behind the cloudflare tunnel
# every TCP connection comes from cloudflared on localhost, so the socket
# address is useless — cloudflare passes the real client in CF-Connecting-IP
# (X-Forwarded-For as a general-proxy fallback). Active only when
# SHOWCASE_PASSWORD is set; local dev is untouched.
MAX_AUTH_FAILURES = 5           # then the IP is locked until backend restart
RATE_WINDOW_S = 10.0
RATE_MAX_REQUESTS = 40          # per IP per window — generous for one human,
                                # a bombardment script trips it immediately
_auth_failures: dict[str, int] = {}
_locked_ips: set[str] = set()
_request_log: dict[str, list[float]] = {}


def _client_ip(request) -> str:
    h = request.headers
    return (
        h.get("cf-connecting-ip")
        or (h.get("x-forwarded-for", "").split(",")[0].strip() or None)
        or (request.client.host if request.client else "unknown")
    )


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    log = _request_log.setdefault(ip, [])
    while log and now - log[0] > RATE_WINDOW_S:
        log.pop(0)
    log.append(now)
    if len(_request_log) > 10000:  # bound memory under address-spoofing floods
        _request_log.clear()
    return len(log) > RATE_MAX_REQUESTS


# Paths that legitimately fire MANY requests in a short burst and must NOT be
# rate-limited, or the app throttles its own normal use: the browser proxy (one
# page = dozens–hundreds of asset sub-requests) and the health/state polls the
# GUI runs on a timer. A heavy SPA (x.com) loading through /proxy otherwise trips
# the 40/10s limit, which then also 429s /health — the GUI reads that as the
# backend being down and appears to "crash-loop" (it never actually crashes).
# The rate limit still guards the real abuse surface (login, /chat, /lab/*).
_RATE_EXEMPT_PREFIXES = ("/proxy", "/health", "/vulnerable", "/rules", "/scenarios", "/assets", "/models")


@app.middleware("http")
async def showcase_basic_auth(request: Request, call_next):
    if not SHOWCASE_PASSWORD:
        return await call_next(request)
    ip = _client_ip(request)
    if ip in _locked_ips:
        return Response(status_code=403,
                        content="Access blocked after too many failed login attempts.")
    path = request.url.path
    rate_exempt = path.startswith(_RATE_EXEMPT_PREFIXES)
    if not rate_exempt and _rate_limited(ip):
        return Response(status_code=429, content="Too many requests — slow down.",
                        headers={"Retry-After": "10"})
    if _cookie_ok(request.cookies):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, password = base64.b64decode(auth[6:]).decode().partition(":")
        except Exception:
            password = ""
        if secrets.compare_digest(password, SHOWCASE_PASSWORD):
            _auth_failures.pop(ip, None)
            response = await call_next(request)
            response.set_cookie(
                _AUTH_COOKIE, _auth_token(), httponly=True, samesite="lax"
            )
            return response
        # Wrong password (not merely missing): count it, lock at the limit.
        _auth_failures[ip] = _auth_failures.get(ip, 0) + 1
        if _auth_failures[ip] >= MAX_AUTH_FAILURES:
            _locked_ips.add(ip)
            print(f"[auth] {ip} LOCKED after {MAX_AUTH_FAILURES} failed attempts", flush=True)
            return Response(status_code=403,
                            content="Access blocked after too many failed login attempts.")
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="networkLLMApp showcase"'},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's ServerErrorMiddleware sits OUTSIDE CORSMiddleware, so a bare
    # 500 from an unhandled exception carries no CORS headers and the browser
    # masks the real error as an opaque "NetworkError". Attach the headers here
    # so the GUI can display the actual detail.
    headers = {}
    origin = request.headers.get("origin")
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
        headers=headers,
    )
topology = TopologyEngine(work_dir=LAB_WORK_DIR)
security = SecurityEngine()
metrics = MetricsCollector()

_active_scenario: Scenario | None = None
_active_model: str = "llama3.1:8b"
# Full-fidelity chat history: user turns, assistant turns WITH their
# tool_calls, and the tool result messages. Persisting only the prose (the
# old behaviour) showed models a conversation where actions apparently happen
# by assertion — qwen2.5 imitated that pattern within a few turns and stopped
# tool calling entirely, fabricating results in prose instead.
_conversation_history: list[dict] = []


def _trim_history() -> None:
    """Keep the rendered prompt safely under num_ctx. Ollama truncates an
    over-long prompt silently from the TOP — system prompt and tool schemas
    go first — so we drop the OLDEST whole user-turn groups ourselves instead.
    Rough estimate: ~3 chars/token; budget leaves room for system prompt,
    tool schemas, and the response."""
    budget_chars = int(OLLAMA_NUM_CTX * 0.7) * 3
    def total() -> int:
        return sum(len(json.dumps(m, default=str)) for m in _conversation_history)
    while _conversation_history and total() > budget_chars:
        cut = 1
        while cut < len(_conversation_history) and _conversation_history[cut].get("role") != "user":
            cut += 1
        print(f"[chat] history over budget — dropping oldest turn ({cut} message(s))", flush=True)
        del _conversation_history[:cut]


# Deterministic response-acceptance guards. Prompt instructions alone do not
# hold (verified live 2026-08-20: qwen2.5:14b drifted into Chinese and claimed
# an unexecuted block DESPITE explicit system-prompt rules against both). A
# rejected response is fed back with a corrective system message and re-asked,
# bounded by MAX_CORRECTIVE_RETRIES; rejected text never enters the shared
# history, so it cannot teach later turns the bad pattern.
MAX_CORRECTIVE_RETRIES = 2
_NON_ENGLISH_RE = re.compile(
    r"[一-鿿぀-ヿ가-힯฀-๿Ѐ-ӿ؀-ۿ]"
)
_ACTION_CLAIM_RE = re.compile(
    # completed-action claims: "I have blocked", "I ran", …
    r"\bI\s*(?:'ve|have|had)?\s*(?:now\s+|just\s+|successfully\s+|already\s+)*"
    r"(?:block|unblock|allow|restor|flush|remov|re-?enabl|appli|add|ran|execut|perform|scann)\w*"
    # passive completion claims: "traffic is now blocked", …
    r"|(?:has\s+been|have\s+been|is\s+now|are\s+now)\s+"
    r"(?:blocked|unblocked|allowed|restored|removed|applied|re-?enabled|flushed|dropped)"
    # announced-intent-then-yield: "I will add a rule", "Let's proceed with
    # blocking" followed by no tool call (observed live — future tense slips
    # past the completion patterns and nothing happens)
    r"|\b(?:I\s+will|I'll|let'?s|let\s+me|proceeding\s+to|going\s+to)\s+(?:now\s+)?"
    r"(?:proceed\s+(?:with|to)\s+)?"
    r"(?:block|unblock|allow|restor|flush|remov|re-?enabl|appli|add|run|test|scan)",
    re.I,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "lab_active": _active_scenario is not None,
        "scenario": _active_scenario.name if _active_scenario else None,
        "firewall_connected": security.connected,
        "firewalls": security.firewall_ids(),
    }


def _load_scenario(scenario_name: str) -> Scenario:
    scenario_file = SCENARIO_DIR / f"{scenario_name}.yaml"
    if not scenario_file.exists():
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_name}' not found")
    raw = yaml.safe_load(scenario_file.read_text())
    return Scenario.model_validate(raw)


def _deploy_and_connect(scenario: Scenario, scenario_name: str) -> dict:
    """Deploy the lab and connect the security engine to the firewall.

    Shared by lab_start and lab_reset. May raise LabAlreadyRunning / RuntimeError
    from topology.start; callers translate those to HTTP errors.
    """
    result = topology.start(scenario)
    # Connect EVERY firewall node (central-hub has one; two-subnet-ixp has two).
    fw_health: dict[str, dict] = {}
    for fw_node in (n for n in scenario.nodes if n.role == "firewall"):
        mgmt_ip = topology.get_mgmt_ip(scenario_name, fw_node.id)
        if not mgmt_ip:
            result.setdefault("firewall_warnings", []).append(
                f"{fw_node.id}: could not resolve mgmt IP"
            )
            continue
        security.connect(fw_node.id, mgmt_url=f"http://{mgmt_ip}:8080")
        for _ in range(30):
            try:
                fw_health[fw_node.id] = security.health(fw_node.id)
                break
            except Exception:
                time.sleep(1)
        else:
            result.setdefault("firewall_warnings", []).append(
                f"{fw_node.id}: firewalld API did not become ready in 30s"
            )
    result["firewall"] = fw_health
    return result


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    SCANNED_NODES.clear()  # nothing is "vulnerable" until scanned in the new lab
    scenario = _load_scenario(scenario_name)
    try:
        result = _deploy_and_connect(scenario, scenario_name)
    except LabAlreadyRunning as exc:
        if _active_scenario is not None:
            # A lab this backend actually manages is up — a genuine
            # double-start, so the 409 is correct.
            raise HTTPException(status_code=409, detail=str(exc))
        # Containers exist but this backend knows of no live lab: stale
        # leftovers from a backend restart or a host reboot (docker's restart
        # policy resurrects them; the firewalls land in the dbus crash-loop).
        # Recover the same way run-experiment.sh does — destroy + redeploy,
        # never restart in place.
        try:
            topology.stop(scenario_name)
        except (LabNotFound, RuntimeError):
            pass
        security.disconnect()
        try:
            result = _deploy_and_connect(scenario, scenario_name)
        except LabAlreadyRunning as exc2:
            raise HTTPException(status_code=409, detail=str(exc2))
        except RuntimeError as exc2:
            raise HTTPException(status_code=500, detail=str(exc2))
        result["recovered_from_stale_lab"] = True
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    return result


@app.post("/lab/stop/{scenario_name}")
def lab_stop(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    SCANNED_NODES.clear()
    try:
        topology.stop(scenario_name)
    except LabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _active_scenario = None
    security.disconnect()
    return {"status": "stopped", "scenario": scenario_name}


@app.post("/lab/reset/{scenario_name}")
def lab_reset(scenario_name: str) -> dict:
    """Full clean slate: destroy + redeploy + clear all in-memory state.

    Deliberately a destroy+redeploy, NOT a container restart — restarting a
    long-lived lab triggers the firewalld dbus crash-loop. Teardown is
    best-effort so a reset still works from a half-broken lab; a genuinely
    still-running lab would surface as LabAlreadyRunning on the redeploy.
    """
    global _active_scenario, _conversation_history
    scenario = _load_scenario(scenario_name)

    # 1. Clear in-memory LLM state (Ollama is stateless per request).
    _conversation_history = []
    SCANNED_NODES.clear()
    # 2. Best-effort destroy of whatever is currently up.
    try:
        topology.stop(scenario_name)
    except (LabNotFound, RuntimeError):
        pass
    security.disconnect()
    _active_scenario = None

    # 3. Redeploy fresh.
    try:
        result = _deploy_and_connect(scenario, scenario_name)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    result["reset"] = True
    return result


@app.get("/lab/state")
def lab_state() -> dict:
    return topology.state()


def _describe_active_drops() -> list[str]:
    """Live DROP rules as node-labelled strings, e.g. ['pc1 -> pc2 (icmp)'].

    Returns [] when a lab is up but no drops exist, so the prompt can say so.
    """
    if not security.connected or _active_scenario is None:
        return []
    ip_to_node = {
        iface.ip.split("/")[0]: n.id
        for n in _active_scenario.nodes
        for iface in n.interfaces
        if iface.ip
    }
    try:
        parsed = security.list_rules().get("parsed", [])
    except Exception:
        return []
    out: list[str] = []
    for r in parsed:
        if r.get("action") != "drop":
            continue
        # Fall back to the website name a public IP was resolved from, so the
        # model sees "pc1a -> example.com" rather than a bare address.
        src = ip_to_node.get(r.get("src_ip"), r.get("src_ip") or "any")
        dst_ip = r.get("dst_ip")
        dst = ip_to_node.get(dst_ip) or EXTERNAL_IP_NAMES.get(dst_ip) or dst_ip or "any"
        proto = r.get("proto") or "all"
        line = f"{src} -> {dst} ({proto})"
        # Label the enforcing firewall only when several exist, so a
        # single-firewall scenario's prompt text stays byte-identical.
        fw = r.get("firewall")
        if fw and len(security.firewall_ids()) > 1:
            line += f" [on {fw}]"
        out.append(line)
    return out


@app.get("/scenarios")
def list_scenarios() -> list[dict]:
    """Enumerate the scenarios/*.yaml the frontend dropdown can choose from.
    Reads name + description cheaply (no full deploy); central-hub sorts first."""
    out: list[dict] = []
    for f in sorted(SCENARIO_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(f.read_text()) or {}
            out.append({"name": raw.get("name", f.stem),
                        "description": raw.get("description", "")})
        except Exception:
            out.append({"name": f.stem, "description": ""})
    out.sort(key=lambda s: (s["name"] != "central-hub", s["name"]))
    return out


@app.get("/scenarios/{scenario_name}")
def get_scenario(scenario_name: str) -> dict:
    """Full node/interface graph for one scenario (the frontend derives the
    topology, subnets, link graph and layout from this — no YAML on the client).
    Reads the YAML; does NOT require the lab to be running."""
    return _load_scenario(scenario_name).model_dump()


@app.get("/vulnerable")
def get_vulnerable() -> dict:
    """Node ids currently flagged vulnerable (point 12) — the topology view
    paints these red with a "(vulnerable)" label. Read live from the running
    containers, so it reflects an LLM fix (run_command upgrade) immediately.
    The GUI refetches this on lab-ready and after any chat tool call."""
    if _active_scenario is None:
        return {"vulnerable": []}
    try:
        return {"vulnerable": vulnerable_nodes(_active_scenario, topology)}
    except Exception:
        return {"vulnerable": []}


@app.get("/rules")
def get_rules() -> dict:
    if not security.connected:
        return {"forward_rules": [], "zone_rules": [], "parsed": []}
    rules = security.list_rules()
    # Label public-internet IPs with the website they were resolved from, so
    # the GUI's rule chips read "pc1a → example.com" instead of a raw address.
    for p in rules.get("parsed", []):
        name = EXTERNAL_IP_NAMES.get(p.get("dst_ip") or "")
        if name:
            p["dst_name"] = name
    return rules


@app.post("/rules/flush")
def flush_rules(firewall: str | None = None) -> dict:
    """Deterministic clean-slate for the rule set (no LLM in the loop).

    Same engine method the LLM's flush_rules tool dispatches to — the
    experiment runner uses this between repetitions so every rep starts from
    an identical firewall state without paying for a full lab redeploy.
    No `firewall` query param = flush EVERY firewall (unchanged behavior);
    `?firewall=fwa` = clear only that firewall (the console panel's button).
    """
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")
    try:
        result = security.flush(fw_id=firewall)
    except RuntimeError as exc:  # unknown firewall id
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "flushed", "firewall": firewall, "result": result}


class RuleRequest(BaseModel):
    src: str
    dst: str
    proto: str = "icmp"
    port: int | None = None
    # Optional explicit target firewall (node id). When omitted, the backend
    # resolves it deterministically from the source's subnet (resolve_firewall).
    firewall: str | None = None


@app.post("/rules")
def add_rule(req: RuleRequest) -> dict:
    """Add a firewall DROP rule from the console form.

    This is the single-surface payoff of the engine refactor: it resolves node
    IDs to IPs and calls `security.block(...)` — the EXACT same engine method the
    LLM's `block_traffic` tool dispatches to (app.chat.dispatch_tool) — using the
    same node validation, the same `_node_ip_map`, and the same `proto` default
    ("icmp"). So a rule added via the form and a rule added via the LLM for the
    same intent are byte-identical, and both mirror into GET /rules (the one
    source the `/` topology view reads).
    """
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")

    validation_err = validate_node_args({"src": req.src, "dst": req.dst}, _active_scenario)
    if validation_err:
        raise HTTPException(status_code=400, detail=validation_err)

    ip_map = _node_ip_map(_active_scenario)
    src_ip = ip_map.get(req.src)
    dst_ip = ip_map.get(req.dst)
    if dst_ip is None:
        # The console form only offers lab nodes; an external hostname here
        # (validate_node_args now lets those through for the LLM path) would
        # otherwise fall through as dst=None = block EVERYTHING from src.
        raise HTTPException(status_code=400,
                            detail=f"'{req.dst}' is not a lab node — use the chat to block websites.")
    fw_id = req.firewall or resolve_firewall(src_ip, _active_scenario)
    result = security.block(src_ip, dst_ip, req.proto, req.port, fw_id=fw_id)
    return {"status": "added", "src": req.src, "dst": req.dst, "proto": req.proto,
            "port": req.port, "firewall": fw_id, "result": result}


# ── In-lab browsing proxy (points 5/10/11): render real sites properly ─────
# The old Browser tab injected a one-shot `wget -O -` into an `<iframe srcDoc
# sandbox="">`: relative asset URLs pointed nowhere (no CSS/JS), scripts were
# blocked by sandbox="", and a large/redirecting response (bearingpoint.com)
# with no size cap could OOM the worker and take the backend down. This proxy
# fixes all three: every page AND asset is fetched from inside the chosen PC
# (curl, HARD-capped size + timeout so nothing can OOM), served back same-origin
# so the browser has no CORS problem, and HTML resource URLs are rewritten to
# point back through the proxy — so CSS, JS and images load and run. The traffic
# still originates in the PC and crosses the firewalls, so blocks still work.
PROXY_MAX_BYTES = 8_000_000       # hard cap per fetch (head -c) — OOM guard
PROXY_TIMEOUT_S = 12
_PROXY_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_CT_BY_EXT = {
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".eot": "application/vnd.ms-fontobject", ".map": "application/json",
    ".webmanifest": "application/manifest+json", ".xml": "application/xml",
}


def _shq(s: str) -> str:
    """Single-quote a string for safe embedding in an sh -c command."""
    return "'" + s.replace("'", "'\\''") + "'"


def _fetch_in_container(node: str, url: str) -> dict:
    """curl the URL from inside a lab PC container, byte-capped and time-bounded.

    Returns {exit, body(bytes), command, container}. The `head -c` cap is the
    real OOM guard — curl --max-filesize only trusts Content-Length, which a
    chunked/streaming response omits, so we bound the pipe itself."""
    container = f"clab-{_active_scenario.name}-{node}"
    inner = (
        f"curl -sSL --compressed --max-redirs 5 --max-time {PROXY_TIMEOUT_S} "
        f"-A {_shq(_PROXY_UA)} -o - {_shq(url)} 2>/dev/null | head -c {PROXY_MAX_BYTES}"
    )
    cmd = ["docker", "exec", container, "sh", "-c", inner]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=PROXY_TIMEOUT_S + 6)
        return {"exit": proc.returncode, "body": proc.stdout,
                "command": f"curl {url}", "container": container}
    except subprocess.TimeoutExpired:
        return {"exit": 124, "body": b"", "command": f"curl {url}", "container": container}


def _proxy_url(node: str, abs_url: str, as_hint: str = "") -> str:
    q = f"/proxy/{quote(node)}?url={quote(abs_url, safe='')}"
    return q + (f"&as={as_hint}" if as_hint else "")


# href/src/action="..." (quoted) and CSS url(...) — enough to reroute the
# stylesheets, scripts, images and fonts that make a page look right.
_ATTR_RE = re.compile(r"""(?P<attr>\b(?:href|src|action|poster)\s*=\s*)(?P<q>["'])(?P<url>[^"']*)(?P=q)""", re.I)
_SRCSET_RE = re.compile(r"""(\bsrcset\s*=\s*)(["'])(.*?)(\2)""", re.I | re.S)
_CSSURL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)


def _rewrite_html(node: str, base_url: str, html: str) -> str:
    def abs_of(u: str) -> str | None:
        u = u.strip()
        if not u or u.startswith(("data:", "mailto:", "javascript:", "tel:", "#", "blob:", "about:")):
            return None
        return urljoin(base_url, u)

    def attr_sub(m: "re.Match") -> str:
        au = abs_of(m.group("url"))
        if au is None:
            return m.group(0)
        # Hint the content type for the two that browsers are strict about.
        low = m.group("url").lower()
        as_hint = "style" if (m.group("attr").lower().startswith("href") and (".css" in low or "css" in low)) else ""
        if m.group("attr").lower().startswith("src") and (".js" in low or "js" in low):
            as_hint = "script"
        return f'{m.group("attr")}{m.group("q")}{_proxy_url(node, au, as_hint)}{m.group("q")}'

    def srcset_sub(m: "re.Match") -> str:
        parts = []
        for cand in m.group(3).split(","):
            cand = cand.strip()
            if not cand:
                continue
            bits = cand.split(None, 1)
            au = abs_of(bits[0])
            if au is None:
                parts.append(cand)
            else:
                parts.append(_proxy_url(node, au, "image") + (f" {bits[1]}" if len(bits) > 1 else ""))
        return f"{m.group(1)}{m.group(2)}{', '.join(parts)}{m.group(4)}"

    def css_sub(m: "re.Match") -> str:
        au = abs_of(m.group(2))
        return m.group(0) if au is None else f"url({_proxy_url(node, au)})"

    # <link rel=stylesheet href=...> is matched by attr_sub with the css hint;
    # <script src=...> with the js hint. Do srcset first so its src= inside
    # isn't double-touched.
    html = _SRCSET_RE.sub(srcset_sub, html)
    html = _ATTR_RE.sub(attr_sub, html)
    html = _CSSURL_RE.sub(css_sub, html)
    # Neutralise <base> tags: they would re-point relative URLs we already
    # rewrote (and any we missed) back at the real origin, which the sandbox
    # then blocks — leaving the page unstyled.
    html = re.sub(r"<base\b[^>]*>", "", html, flags=re.I)
    footer = (
        "<div style=\"position:fixed;left:0;right:0;bottom:0;z-index:2147483647;"
        "font:11px/1.6 monospace;background:#0f172a;color:#94a3b8;padding:2px 8px;"
        "opacity:.9\">served live from container "
        f"<b style=\"color:#e2e8f0\">{node}</b> &middot; every asset fetched inside the lab PC</div>"
    )
    if "</body>" in html.lower():
        idx = html.lower().rfind("</body>")
        html = html[:idx] + footer + html[idx:]
    else:
        html += footer
    return html


@app.get("/proxy/{node}")
def proxy(node: str, url: str, request: Request):
    """Fetch `url` from inside lab PC `node` and serve it same-origin, rewriting
    HTML so its CSS/JS/images load through this proxy too. The `as` query param
    hints the content type for stylesheet/script/image sub-requests — read raw
    because `as` is a Python keyword and can't be a function parameter name."""
    as_hint = request.query_params.get("as", "")
    if _active_scenario is None:
        return Response("No lab is running.", status_code=400)
    src = next((n for n in _active_scenario.nodes if n.id == node), None)
    if src is None or src.role != "pc":
        return Response("Browsing source must be a lab PC.", status_code=400)

    raw = url.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return Response(f"Not a valid http/https URL: {url}", status_code=400)

    result = _fetch_in_container(node, raw)
    body: bytes = result["body"]

    # Failed fetch (blocked by a firewall, timeout, refused): return a styled
    # error page so the iframe shows the block clearly — the demo's payoff.
    if result["exit"] != 0 or not body:
        host = parts.hostname
        page = f"""<!doctype html><html><head><meta charset=utf-8><title>Can't reach {host}</title>
<style>body{{font:16px system-ui,sans-serif;color:#334155;background:#f8fafc;height:100vh;margin:0;
display:flex;align-items:center;justify-content:center}}.b{{max-width:30rem;text-align:center;padding:2rem}}
.i{{font-size:3rem;color:#cbd5e1}}code{{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:4px;padding:2px 6px;font-size:.85em}}</style>
</head><body><div class=b><div class=i>&#9888;</div><h2>This site can't be reached</h2>
<p><code>{host}</code> took too long to respond or the connection was refused.</p>
<p style="font-size:.85em;color:#64748b">The request really ran inside <b>{node}</b> and was dropped on
the network path — this is what an active firewall block looks like.</p></div></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    # Decide content type. Top-level documents are HTML; sub-resources use the
    # hint (strict for css/js) then the URL extension, then a light sniff.
    path = parts.path.lower()
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    looks_html = body[:512].lstrip()[:1].lower() in (b"<",) and (
        b"<html" in body[:2048].lower() or b"<!doctype" in body[:512].lower()
    )
    if as_hint == "style":
        ctype = "text/css; charset=utf-8"
    elif as_hint == "script":
        ctype = "text/javascript; charset=utf-8"
    elif ext in _CT_BY_EXT:
        ctype = _CT_BY_EXT[ext]
    elif as_hint == "image" or body[:4] in (b"\x89PNG", b"GIF8") or body[:3] == b"\xff\xd8\xff":
        ctype = "image/png" if body[:4] == b"\x89PNG" else ("image/gif" if body[:4] == b"GIF8" else "image/jpeg")
    elif looks_html or (not ext and not as_hint):
        ctype = "text/html; charset=utf-8"
    else:
        ctype = "application/octet-stream"

    if ctype.startswith("text/html"):
        html = body.decode("utf-8", errors="replace")
        html = _rewrite_html(node, raw, html)
        return Response(html, media_type="text/html; charset=utf-8")
    if ctype.startswith("text/css"):
        css = body.decode("utf-8", errors="replace")
        # Rewrite url(...) inside CSS so background images / @font-face resolve.
        css = _CSSURL_RE.sub(
            lambda m: (lambda au: m.group(0) if au is None else f"url({_proxy_url(node, au)})")(
                None if (not m.group(2).strip() or m.group(2).startswith("data:"))
                else urljoin(raw, m.group(2).strip())
            ),
            css,
        )
        return Response(css, media_type="text/css; charset=utf-8")
    return Response(content=body, media_type=ctype)


class BrowseRequest(BaseModel):
    node: str  # the lab PC doing the browsing
    url: str   # http[s]://<host>[:port][/path] — lab hosts OR the real internet


@app.post("/browse")
def browse(req: BrowseRequest) -> dict:
    """Fetch a web page FROM INSIDE a lab PC (the GUI's Browser tab).

    This is a REAL request, not a simulation: `wget` runs inside the source
    container and the traffic crosses the routed PC->switch->firewall path,
    so an active DROP rule visibly kills the page load.

    Targets may be lab hosts (node names resolve via the container's own
    /etc/hosts to data-plane IPs) or PUBLIC INTERNET sites: since the WAN-
    Interconnect gateway (2026-08-25) the lab's default route runs over the
    data plane through the firewalls to a NAT uplink, so a real site loads —
    and blocks against it genuinely stop it — with nothing simulated.
    """
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")

    src = next((n for n in _active_scenario.nodes if n.id == req.node), None)
    if src is None or src.role != "pc":
        pcs = sorted(n.id for n in _active_scenario.nodes if n.role == "pc")
        raise HTTPException(status_code=400, detail=f"Browsing source must be a lab PC. Valid: {pcs}")

    raw_url = req.url.strip()
    if "://" not in raw_url:
        raw_url = f"http://{raw_url}"
    parts = urlsplit(raw_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail=f"Not a valid http/https URL: '{req.url}'")

    # Label who serves this: a lab node, or the internet.
    ip_map = _node_ip_map(_active_scenario)
    aliases = alias_map(_active_scenario)
    host = parts.hostname.lower()
    by_ip = {ip: node for node, ip in ip_map.items()}
    server_node = (
        host if host in ip_map else aliases.get(host) or by_ip.get(host) or "internet"
    )

    # Byte-cap the fetch so a huge/streaming response can't OOM the worker
    # (bearingpoint.com crashed the backend this way — point 10). `head -c`
    # bounds the pipe itself; the GUI uses this only for reachability/metadata
    # and renders the real page through /proxy.
    tls = "--no-check-certificate " if parts.scheme == "https" else ""
    inner = f"wget -q -O - -T 6 {tls}{_shq(raw_url)} | head -c 2000000"
    cmd = ["sh", "-c", inner]
    started = time.monotonic()
    result = topology.exec(_active_scenario.name, req.node, cmd)
    elapsed = round(time.monotonic() - started, 2)

    # The pipe to `head` makes the shell's exit code head's (0), so reachability
    # is judged by whether any bytes came back: a firewall DROP / timeout yields
    # an empty body. This is what drives the GUI's "can't be reached" panel.
    body = result["stdout"] or ""
    ok = bool(body.strip())
    return {
        "ok": ok,
        "exit": result["exit"],
        "html": body if ok else None,
        "error": None if ok else (result["stderr"].strip() or "no response — the request was dropped or timed out"),
        "elapsed_s": elapsed,
        "url": raw_url,
        "node": req.node,
        "server_node": server_node,
        "container": result["container"],
        "command": f"wget {raw_url}",
    }


@app.websocket("/ws/console/{scenario_name}/{node}")
async def console_ws(websocket: WebSocket, scenario_name: str, node: str) -> None:
    """A real PTY shell into a lab container, bridged over a WebSocket.

    Transport = a genuine kernel PTY (`pty.openpty()`) wrapped around
    `docker exec -it <container> <shell>`, NOT a simulation: line editing,
    colours, `clear`, `vim`, `ping`, `firewall-cmd` all work for real. Output
    is pumped async-without-threads — `loop.add_reader(master, …)` wakes us on
    container output, we enqueue it, and a single writer task drains the queue
    so sends stay ordered and never overlap on the one socket. Protocol is JSON
    frames: {"type":"input","data":str} and {"type":"resize","cols":int,"rows":int}.
    """
    # BaseHTTPMiddleware never sees WebSocket scopes, so the showcase auth
    # must be enforced here via the cookie set on the authenticated page load.
    if SHOWCASE_PASSWORD and not _cookie_ok(websocket.cookies):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    scenario = _active_scenario
    if scenario is None or scenario.name != scenario_name:
        await websocket.close(code=4404)
        return
    node_obj = next((n for n in scenario.nodes if n.id == node), None)
    if node_obj is None:
        await websocket.close(code=4404)
        return

    # fw has /usr/bin/bash; alpine PCs/router are busybox (sh only).
    shell = "/usr/bin/bash" if node_obj.role == "firewall" else "/bin/sh"
    container = f"clab-{scenario_name}-{node}"

    master, slave = pty.openpty()
    # Sane initial window size; the frontend sends a resize frame on connect.
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    def _child_setup() -> None:
        # New session + make the slave (now fd 0/1/2) the controlling TTY, so
        # the `docker exec` client receives SIGWINCH on TIOCSWINSZ and forwards
        # the resize into the container's TTY. Without this, resize is a no-op.
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        ["docker", "exec", "-it", container, shell],
        stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=_child_setup,
    )
    os.close(slave)
    os.set_blocking(master, False)

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if data:
            out_queue.put_nowait(data)
        else:
            # EOF: the container shell exited / the pty closed.
            loop.remove_reader(master)
            out_queue.put_nowait(None)

    loop.add_reader(master, on_readable)

    async def pump_output() -> None:
        try:
            while True:
                data = await out_queue.get()
                if data is None:
                    break
                await websocket.send_text(data.decode(errors="replace"))
        finally:
            # Unblock the input loop's receive_text() when the shell ended.
            try:
                await websocket.close()
            except RuntimeError:
                pass

    writer = asyncio.create_task(pump_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ftype = frame.get("type")
            if ftype == "input":
                try:
                    os.write(master, frame.get("data", "").encode())
                except OSError:
                    break
            elif ftype == "resize":
                try:
                    cols = int(frame.get("cols", 80))
                    rows = int(frame.get("rows", 24))
                    fcntl.ioctl(
                        master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0),
                    )
                except (OSError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        try:
            loop.remove_reader(master)
        except (ValueError, OSError):
            pass
        out_queue.put_nowait(None)
        writer.cancel()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.close(master)
        except OSError:
            pass


class ChatRequest(BaseModel):
    message: str
    model: str = "llama3.1:8b"
    # Ollama sampling options (temperature, seed, ...) — used by the experiment
    # runner to fix temperature across repetitions. None = Ollama defaults,
    # so GUI behavior is unchanged.
    options: dict | None = None


@app.post("/chat/reset")
def chat_reset() -> dict:
    global _conversation_history
    _conversation_history = []
    return {"status": "cleared"}


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    global _active_model
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")

    _active_model = req.model
    _trim_history()
    turn_start = len(_conversation_history)
    _conversation_history.append({"role": "user", "content": req.message})

    # Inject the live DROP rules into the system prompt as ground-truth data so
    # the model re-enables the exact pair/proto the user blocked (it otherwise
    # hallucinates the wrong src node or downgrades proto to "all" on re-enable).
    active_drops = _describe_active_drops()
    messages = [
        {"role": "system", "content": build_system_prompt(_active_scenario, active_drops)}
    ] + _conversation_history

    all_tool_results: list[dict] = []
    first_llm_at: float | None = None
    prompt_sent_at = time.time()

    # Two separate budgets: tool rounds (unchanged semantics — bounds how many
    # execute-and-reprompt cycles a turn may take) and corrective retries (the
    # acceptance guard below; rejected responses don't consume tool rounds).
    iteration = 0
    tool_rounds = 0
    corrective_retries = 0
    while True:
        print(
            f"[chat] iter={iteration} sending {len(messages)} messages; "
            f"last role={messages[-1].get('role')!r}",
            flush=True,
        )
        if iteration > 0:
            print(
                f"[chat] iter={iteration} tail messages: "
                f"{json.dumps(messages[-3:], default=str)[:1500]}",
                flush=True,
            )

        interaction = metrics.start_interaction(req.model, req.message)

        try:
            ollama_resp = call_ollama(req.model, messages, req.options)
        except Exception as e:
            # Roll back the ENTIRE in-flight turn (user message and any
            # already-persisted assistant/tool messages from earlier
            # iterations). A failed call (e.g. a cold-start timeout) must not
            # leave a half-finished turn in the shared history — a thread with
            # consecutive user turns and no assistant/tool replies was the
            # root cause of allow_traffic silently no-op'ing on a later
            # "re-enable" turn.
            del _conversation_history[turn_start:]
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

        now = time.time()
        if first_llm_at is None:
            first_llm_at = now
        interaction["llm_response_at"] = now
        interaction["prompt_eval_count"] = ollama_resp.get("prompt_eval_count")
        interaction["eval_count"] = ollama_resp.get("eval_count")

        msg = ollama_resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])
        salvaged = False
        if not tool_calls:
            # Model wrote a tool call as JSON text instead of emitting it
            # (qwen2.5 failure mode) — recover and EXECUTE it so the claim
            # becomes the action instead of a fabricated result.
            salvaged_calls, cleaned = salvage_content_tool_calls(msg.get("content", ""))
            if salvaged_calls:
                salvaged = True
                msg = {"role": "assistant", "content": cleaned, "tool_calls": salvaged_calls}
                tool_calls = salvaged_calls
                print(
                    f"[chat] iter={iteration} SALVAGED {len(salvaged_calls)} tool call(s) "
                    f"written as JSON text: {[c['function']['name'] for c in salvaged_calls]}",
                    flush=True,
                )
        print(
            f"[chat] iter={iteration} response role={msg.get('role')!r} "
            f"content={msg.get('content', '')!r} tool_calls={len(tool_calls)} "
            f"done_reason={ollama_resp.get('done_reason')!r}",
            flush=True,
        )
        if not tool_calls and not msg.get("content"):
            # Fires on iteration 0 too: the "explain the topology" bug is an
            # EMPTY reply on the very first turn (the model answers nothing and
            # calls no tool), so gating this on iteration>0 hid the actual case.
            print(
                f"[chat] iter={iteration} FULL RESPONSE (empty content + no tool calls): "
                f"{json.dumps(ollama_resp, default=str)[:2000]}",
                flush=True,
            )

        if not tool_calls:
            assistant_content = msg.get("content", "")

            # Acceptance guard: reject and re-ask instead of returning (and
            # persisting) a response that breaks a hard invariant.
            not_english = bool(_NON_ENGLISH_RE.search(assistant_content))
            false_claim = bool(
                not all_tool_results and _ACTION_CLAIM_RE.search(assistant_content)
            )
            # Empty-reply guard (point 1): some models (observed: "explain the
            # topology" as a FIRST turn) return no content AND no tool call —
            # the GUI then shows a blank bubble. Retry with a nudge to either
            # call describe_state or actually answer. Only when nothing has run
            # this turn (an empty final content after real tool rounds is fine —
            # the tool results already answered).
            empty_reply = not assistant_content.strip() and not all_tool_results
            if (not_english or false_claim or empty_reply) and corrective_retries < MAX_CORRECTIVE_RETRIES:
                corrective_retries += 1
                iteration += 1
                if empty_reply:
                    reason = "empty reply"
                    messages.append({
                        "role": "user",
                        "content": "Your previous reply was empty. If I asked about "
                                   "the network topology or its state, call the "
                                   "describe_state tool and then explain the result "
                                   "in plain English. Otherwise, answer my question "
                                   "directly in English. Never reply with nothing.",
                    })
                    print(
                        f"[chat] iter={iteration} REJECTED response (empty reply) — "
                        f"corrective retry {corrective_retries}/{MAX_CORRECTIVE_RETRIES}",
                        flush=True,
                    )
                    interaction["prose_response"] = "[retracted: empty reply]"
                    interaction["execution_success"] = True
                    metrics.record(interaction)
                    continue
                if not_english:
                    reason = "response not in English"
                    # Keep the draft visible — the model must translate it.
                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({
                        "role": "user",
                        "content": "Rewrite your previous reply in English only. "
                                   "Keep exactly the same information.",
                    })
                else:
                    reason = "claimed an action but no tool ran this turn"
                    # Do NOT append the bad draft: at low temperature the model
                    # anchors on it and repeats it verbatim (observed live —
                    # two system-role corrections were ignored word-for-word).
                    # A user-role correction with a fresh slate works instead.
                    messages.append({
                        "role": "user",
                        "content": "Your reply was discarded: you claimed an action or "
                                   "result, but you called NO tool, so nothing was "
                                   "executed and no result exists. My request was: "
                                   f"\"{req.message}\". If it requires an action or "
                                   "test, emit the appropriate tool call(s) NOW. "
                                   "Only describe an action as done after a tool call "
                                   "executed it.",
                    })
                print(
                    f"[chat] iter={iteration} REJECTED response ({reason}) — "
                    f"corrective retry {corrective_retries}/{MAX_CORRECTIVE_RETRIES}",
                    flush=True,
                )
                interaction["prose_response"] = f"[retracted: {reason}] {assistant_content}"
                interaction["execution_success"] = True
                metrics.record(interaction)
                continue

            if false_claim:
                # Retries exhausted and the model still claims an unexecuted
                # action: deterministically tell the truth. The disclaimer is
                # returned AND persisted, so neither the user nor later turns
                # inherit the false claim.
                assistant_content += (
                    "\n\n⚠️ Note: no tool was actually executed this turn, so no "
                    "change or test happened on the network. Please ask again to "
                    "perform the action."
                )
                print("[chat] retries exhausted — false-claim disclaimer appended", flush=True)

            if not assistant_content.strip() and not all_tool_results:
                # Empty reply survived the retries: never return a blank bubble.
                assistant_content = (
                    "I didn't produce a reply for that. Could you rephrase? "
                    "For example, ask me to \"describe the network\" and I'll show "
                    "the current topology and firewall state."
                )
                print("[chat] retries exhausted — empty-reply fallback appended", flush=True)

            interaction["prose_response"] = assistant_content
            interaction["execution_success"] = True
            metrics.record(interaction)

            _conversation_history.append({"role": "assistant", "content": assistant_content})

            return {
                "response": assistant_content,
                "tool_calls": all_tool_results,
                "metrics": {
                    "llm_latency_s": round(first_llm_at - prompt_sent_at, 3),
                    "total_latency_s": round(time.time() - prompt_sent_at, 3),
                },
            }

        messages.append(msg)
        _conversation_history.append(msg)

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})

            per_tool = metrics.start_interaction(req.model, req.message)
            per_tool["tool_call"] = name
            per_tool["tool_args"] = str(args)
            per_tool["salvaged"] = 1 if salvaged else 0

            validation_err = validate_node_args(args, _active_scenario)
            if validation_err:
                per_tool["validation_passed"] = False
                per_tool["validation_error"] = validation_err
                per_tool["execution_success"] = False
                metrics.record(per_tool)
                tool_output = {"error": validation_err}
                all_tool_results.append({"tool": name, "error": validation_err})
            else:
                per_tool["validation_passed"] = True
                try:
                    tool_result = dispatch_tool(
                        name, args, _active_scenario, security, topology,
                    )
                    per_tool["tool_executed_at"] = time.time()
                    per_tool["execution_result"] = str(tool_result)[:500]
                    per_tool["execution_success"] = True
                    metrics.record(per_tool)
                    tool_output = tool_result
                    all_tool_results.append({"tool": name, "args": args, "result": tool_result})
                except Exception as e:
                    per_tool["tool_executed_at"] = time.time()
                    per_tool["execution_result"] = str(e)[:500]
                    per_tool["execution_success"] = False
                    metrics.record(per_tool)
                    tool_output = {"error": str(e)}
                    all_tool_results.append({"tool": name, "args": args, "error": str(e)})

            tool_msg = {"role": "tool", "content": json.dumps(tool_output)}
            messages.append(tool_msg)
            _conversation_history.append(tool_msg)

        interaction["salvaged"] = 1 if salvaged else 0
        interaction["execution_success"] = True
        metrics.record(interaction)

        iteration += 1
        tool_rounds += 1
        if tool_rounds > MAX_TOOL_ITERATIONS:
            break

    # Tool-round budget exhausted: the assistant msg + tool results of the
    # final round are already persisted above — history stays as-is.
    final_content = msg.get("content", "") or "Actions completed."

    return {
        "response": final_content,
        "tool_calls": all_tool_results,
        "metrics": {
            "llm_latency_s": round((first_llm_at or prompt_sent_at) - prompt_sent_at, 3),
            "total_latency_s": round(time.time() - prompt_sent_at, 3),
        },
    }


@app.get("/models")
def list_models() -> dict:
    """Models installed on whatever Ollama answers :11434 — the local service
    or the GPU tunnel. Drives the GUI's model dropdown."""
    base = OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base}/api/tags")
            resp.raise_for_status()
            names = [m["name"] for m in resp.json().get("models", [])]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {exc}") from exc
    # Showcase hosting: SHOWCASE_MODEL (set by showcase-remote.sh) is the model
    # provisioned + warmed for the event — the GUI selects it by default so the
    # first question doesn't land on a cold model. Absent in local dev.
    default = os.environ.get("SHOWCASE_MODEL")
    return {"models": names, "default": default if default in names else None}


@app.get("/metrics/session")
def get_session_metrics() -> dict:
    return {"session_id": metrics.session_id, "interactions": metrics.get_session_metrics()}


@app.get("/metrics/models")
def get_model_summary() -> list[dict]:
    return metrics.get_model_summary()


# ── Built GUI (single-port hosting) ───────────────────────────────────────
# When gui/dist exists (npm run build), the backend serves the GUI itself, so
# one port (8000) carries the whole app — the shape remote hosting needs
# (vast.ai open port / tunnel = ONE public URL). Registered last: mounts are
# matched after the API routes above, so "/" falls through to index.html only
# when no API route matches. Local dev (vite on :5173) is unaffected.
_GUI_DIST = REPO_ROOT / "gui" / "dist"
if _GUI_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_GUI_DIST, html=True), name="gui")
