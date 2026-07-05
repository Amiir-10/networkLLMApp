"""Deterministic reachability probing — the ground-truth judge.

Probes are docker-exec pings between DATA-plane IPs, never node names
(names resolve via /etc/hosts entries; probing IPs directly sidesteps any
resolution concern) and never through the LLM's ping_test tool. ICMP-only
in v1; per-protocol probes (curl 80/443, UDP 9999) are phase 2.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor


def pc_ips(scenario: dict) -> dict[str, str]:
    """PC node id -> data-plane IP, from a GET /scenarios/{name} dump."""
    out: dict[str, str] = {}
    for node in scenario["nodes"]:
        if node.get("role") != "pc":
            continue
        for iface in node.get("interfaces", []):
            if iface.get("ip"):
                out[node["id"]] = iface["ip"].split("/")[0]
                break
    return out


def _ping(scenario_name: str, src: str, dst_ip: str) -> bool:
    cmd = ["docker", "exec", f"clab-{scenario_name}-{src}",
           "ping", "-c", "1", "-W", "1", dst_ip]
    rc = subprocess.run(cmd, capture_output=True).returncode
    if rc == 0:
        return True
    # One retry: a single lost packet must not corrupt satisfiability.
    return subprocess.run(cmd, capture_output=True).returncode == 0


def probe_matrix(scenario_name: str, pcs: dict[str, str], workers: int = 10) -> dict[str, bool]:
    """Reachability over all ordered PC pairs. Keys are 'src->dst' (JSON-safe)."""
    pairs = [(s, d) for s in pcs for d in pcs if s != d]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = ex.map(lambda p: _ping(scenario_name, p[0], pcs[p[1]]), pairs)
    return {f"{s}->{d}": ok for (s, d), ok in zip(pairs, results)}


def expected_matrix(pcs: dict[str, str], unreachable: list[list[str]]) -> dict[str, bool]:
    """All ordered PC pairs reachable, minus the authored unreachable delta."""
    blocked = {f"{s}->{d}" for s, d in unreachable}
    return {f"{s}->{d}": f"{s}->{d}" not in blocked
            for s in pcs for d in pcs if s != d}
