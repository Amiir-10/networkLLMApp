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


def launch_service(container: str, command: str) -> subprocess.CompletedProcess:
    """Start a node's declared `launch` command detached inside the container,
    AFTER L3 is configured. Generalises the old hard-coded :8080 listener: a
    scenario node now says what service to run (for hosts whose service is not
    the image's own PID 1). Detached + best-effort, so lab readiness never
    depends on it."""
    return docker_exec_detached(container, ["sh", "-c", command])


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


def write_data_plane_hosts(scenario_name: str, scenario: Scenario) -> list[str]:
    """Point every node name at its DATA-plane IP inside each container's /etc/hosts.

    containerlab/Docker resolve a node name to its MANAGEMENT IP (172.20.20.x),
    and the mgmt bridge connects all containers directly — so an operator's
    `ping <name>` in the console goes over mgmt and NEVER traverses the firewall,
    silently bypassing DROP rules. We append `<data-ip> <name>` lines so name
    lookups hit the routed data plane instead. nsswitch resolves `files` before
    `dns`, so these win over the mgmt-IP DNS entries. The block tool already pings
    data IPs (_node_ip_map); this makes a human's by-name ping match it.

    Best-effort: a node with no L3 interface (a switch) contributes no entry, and
    a per-container write failure is returned as a warning, never raised.
    """
    name_ip: dict[str, str] = {}
    for node in scenario.nodes:
        for iface in node.interfaces:
            if iface.ip:
                name_ip[node.id] = iface.ip.split("/")[0]
                # Scenario-declared aliases (e.g. example.com -> pc1b) resolve to
                # the same data-plane IP, so browsing them crosses the firewall.
                for alias in node.aliases:
                    name_ip[alias] = iface.ip.split("/")[0]
                break  # primary (first L3) interface, same pick as _node_ip_map
    if not name_ip:
        return []
    block = "\n".join(f"{ip}\t{name}" for name, ip in name_ip.items())
    # Heredoc append: robust against the embedded newlines/whitespace, works on
    # busybox (alpine) and debian alike. Containers are fresh per deploy, so
    # /etc/hosts starts clean — no accumulation across redeploys.
    append_cmd = f"cat >> /etc/hosts <<'CLAB_DP_HOSTS'\n{block}\nCLAB_DP_HOSTS"
    warnings: list[str] = []
    for node in scenario.nodes:
        container = f"clab-{scenario_name}-{node.id}"
        r = docker_exec(container, ["sh", "-c", append_cmd])
        if r.returncode != 0:
            warnings.append(f"{container}: write data-plane hosts -> {r.stderr.strip()}")
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

        # A switch is pure L2: bridge all its data interfaces (eth1..ethN), no IP,
        # no routes. Members are brought up AFTER disable_ipv6 set all.disable_ipv6=1,
        # so the bridged ports stay IPv6-free.
        if node.role == "switch":
            for cmd in (["ip", "link", "add", "br0", "type", "bridge"],
                        ["ip", "link", "set", "br0", "up"]):
                r = docker_exec(container, cmd)
                if r.returncode != 0 and "File exists" not in r.stderr:
                    warnings.append(f"{container}: {' '.join(cmd)} -> {r.stderr.strip()}")
            for idx in range(1, len(node.interfaces) + 1):
                eth = f"eth{idx}"
                for cmd in (["ip", "link", "set", eth, "master", "br0"],
                            ["ip", "link", "set", eth, "up"]):
                    r = docker_exec(container, cmd)
                    if r.returncode != 0:
                        warnings.append(f"{container}: {' '.join(cmd)} -> {r.stderr.strip()}")
            continue  # no L3, no static routes, no launch on a switch

        if node.role == "firewall":
            if not wait_for_firewalld(container):
                warnings.append(f"{container}: firewalld did not become ready in time")
        for idx, iface in enumerate(node.interfaces, start=1):
            eth = f"eth{idx}"
            if not iface.ip:
                continue  # defensive: only switches are IP-less (handled above)
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
        # Extra static routes (multi-subnet: reach a non-adjacent subnet via a
        # specific next-hop). Applied after interfaces so the next-hops are up.
        for route in node.routes:
            rr = docker_exec(container, ["ip", "route", "replace", route.to, "via", route.via])
            if rr.returncode != 0:
                warnings.append(
                    f"{container}: ip route replace {route.to} via {route.via} -> {rr.stderr.strip()}"
                )
        if node.launch:
            # Best-effort: a failure here must not block lab readiness.
            rl = launch_service(container, node.launch)
            if rl.returncode != 0:
                warnings.append(f"{container}: service launch -> {rl.stderr.strip()}")
    # Final pass: now that every node's data-plane IP is up, point node names at
    # those IPs so console `ping <name>` traverses the firewall (not mgmt).
    warnings.extend(write_data_plane_hosts(scenario_name, scenario))
    return warnings
