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

# IPv6 standing rule (Amir, 2026-05-30): IPv6 is disabled in EVERY container, on
# every topology, regardless of image. These keys are the single source of truth —
# topology.py stamps them into every node's containerlab `sysctls` (primary,
# applied at creation so it holds on any image), and disable_ipv6() re-applies
# them inside each running container post-deploy (belt-and-suspenders).
IPV6_DISABLE_SYSCTLS = {
    "net.ipv6.conf.all.disable_ipv6": 1,
    "net.ipv6.conf.default.disable_ipv6": 1,
    "net.ipv6.conf.lo.disable_ipv6": 1,
}

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

    Complements the topology-generator sysctls (which apply at creation) by
    re-asserting them on the live container and flushing any IPv6 address that
    slipped onto an interface containerlab attached after creation. Best-effort:
    failures are returned as warnings, never raised. Writes /proc directly so it
    works on busybox (alpine) and procps (debian) alike.
    """
    warnings: list[str] = []
    for scope in ("all", "default", "lo"):
        proc_path = f"/proc/sys/net/ipv6/conf/{scope}/disable_ipv6"
        r = docker_exec(container, ["sh", "-c", f"echo 1 > {proc_path}"])
        # A container with IPv6 compiled out has no such path — that's already
        # "disabled", so a missing path is not a warning.
        if r.returncode != 0 and "No such file" not in r.stderr:
            warnings.append(f"{container}: disable_ipv6 {scope} -> {r.stderr.strip()}")
    # Drop any residual IPv6 addrs (e.g. link-local) on already-up interfaces.
    docker_exec(container, ["sh", "-c", "ip -6 addr flush scope global 2>/dev/null; "
                                        "ip -6 addr flush scope link 2>/dev/null || true"])
    return warnings


def configure_nodes(scenario_name: str, scenario: Scenario) -> list[str]:
    """Apply per-node L3 config (IP/link/route) + launch PC services.

    Verbatim relocation of ContainerlabLabDriver._configure_node — same commands,
    same ordering, same best-effort warning collection.
    """
    warnings: list[str] = []
    for node in scenario.nodes:
        container = f"clab-{scenario_name}-{node.id}"
        # Standing rule: IPv6 off on every node, every image (belt-and-suspenders
        # to the containerlab sysctls), before bringing interfaces up.
        warnings.extend(disable_ipv6(container))
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
