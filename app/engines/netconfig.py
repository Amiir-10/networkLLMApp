"""Network-config engine: per-node L3 setup + service launch.

Extracted verbatim from ContainerlabLabDriver._configure_node so the same
container-level commands run, just from a standalone module. The three
`docker exec` wrappers are pure (no instance state), so topology.py imports the
SAME functions — there is one copy of "how we talk to a container", not two.

disable_ipv6() exists as the home for the IPv6 standing rule (Phase 2a); it is a
no-op placeholder here so Phase 1 stays strictly behavior-preserving.
"""
import subprocess
import time

from app.lab.models import Scenario

# A tiny TCP service every PC runs so the hosts are not empty idle containers:
# they listen on :8080 and answer each connection. Run detached (PID 1 stays
# `sleep infinity`, so the demo's connectivity never depends on this), it gives
# a real, reachable service for future network/port scans to discover.
PC_LISTENER_SCRIPT = (
    "while true; do "
    "printf 'HTTP/1.1 200 OK\\r\\nConnection: close\\r\\n\\r\\nalive: %s\\n' \"$(hostname)\" "
    "| nc -l -p 8080 2>/dev/null; "
    "done"
)


def docker_exec(container: str, cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def docker_exec_detached(container: str, cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-d", container, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def wait_for_firewalld(container: str, timeout: int = 30) -> bool:
    for _ in range(timeout * 2):
        r = docker_exec(container, ["firewall-cmd", "--state"], timeout=5)
        if r.returncode == 0 and "running" in r.stdout:
            return True
        time.sleep(0.5)
    return False


def launch_pc_listener(container: str) -> subprocess.CompletedProcess:
    """Start the always-on :8080 listener (detached) so the PC is a live,
    reachable host rather than an idle container."""
    return docker_exec_detached(container, ["sh", "-c", PC_LISTENER_SCRIPT])


def disable_ipv6(container: str) -> list[str]:
    """Belt-and-suspenders IPv6 disable inside a running container.

    Phase 1: no-op (kept behavior-preserving). Phase 2a fills this in with
    `sysctl -w net.ipv6.conf.{all,default,lo}.disable_ipv6=1`, complementing the
    topology-generator sysctls so the standing rule holds on any image.
    """
    return []


def configure_nodes(scenario_name: str, scenario: Scenario) -> list[str]:
    """Apply per-node L3 config (IP/link/route) + launch PC services.

    Verbatim relocation of ContainerlabLabDriver._configure_node — same commands,
    same ordering, same best-effort warning collection.
    """
    warnings: list[str] = []
    for node in scenario.nodes:
        container = f"clab-{scenario_name}-{node.id}"
        if node.role == "firewall":
            if not wait_for_firewalld(container):
                warnings.append(f"{container}: firewalld did not become ready in time")
        for idx, iface in enumerate(node.interfaces, start=1):
            eth = f"eth{idx}"
            r1 = docker_exec(container, ["ip", "addr", "add", iface.ip, "dev", eth])
            if r1.returncode != 0 and "File exists" not in r1.stderr:
                warnings.append(f"{container}: ip addr add {iface.ip} dev {eth} -> {r1.stderr.strip()}")
            r2 = docker_exec(container, ["ip", "link", "set", eth, "up"])
            if r2.returncode != 0:
                warnings.append(f"{container}: ip link set {eth} up -> {r2.stderr.strip()}")
            if iface.gateway:
                r3 = docker_exec(
                    container, ["ip", "route", "replace", "default", "via", iface.gateway]
                )
                if r3.returncode != 0:
                    warnings.append(
                        f"{container}: ip route replace default via {iface.gateway} -> {r3.stderr.strip()}"
                    )
        if node.role == "pc":
            # Best-effort: a failure here must not block lab readiness.
            rl = launch_pc_listener(container)
            if rl.returncode != 0:
                warnings.append(f"{container}: listener launch -> {rl.stderr.strip()}")
    return warnings
