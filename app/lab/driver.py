import json
import subprocess
import time
from pathlib import Path
from typing import Protocol

import yaml

from app.lab.models import Scenario

CONTAINERLAB_BIN = "/home/amir/.local/bin/containerlab"


class LabAlreadyRunning(Exception):
    pass


class LabNotFound(Exception):
    pass


class LabDriver(Protocol):
    def start(self, scenario: Scenario) -> dict: ...
    def stop(self, scenario_name: str) -> None: ...
    def state(self) -> dict: ...
    def exec(self, scenario_name: str, node_id: str, cmd: list[str]) -> dict: ...


class ContainerlabLabDriver:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _lab_dir(self, name: str) -> Path:
        d = self.work_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _topology_file(self, name: str) -> Path:
        return self._lab_dir(name) / f"{name}.clab.yml"

    def _to_containerlab(self, scenario: Scenario) -> dict:
        topology: dict = {"nodes": {}, "links": []}
        iface_index: dict[tuple[str, str], int] = {}
        for node in scenario.nodes:
            if node.role == "firewall":
                clab_node: dict = {
                    "kind": "linux",
                    "image": node.image,
                    "sysctls": {"net.ipv4.ip_forward": 1},
                }
            else:
                clab_node = {
                    "kind": "linux",
                    "image": node.image,
                    "cmd": "sleep infinity",
                }
            topology["nodes"][node.id] = clab_node
            for idx, iface in enumerate(node.interfaces, start=1):
                iface_index[(node.id, iface.to)] = idx
        seen: set[tuple[str, str]] = set()
        for node in scenario.nodes:
            for iface in node.interfaces:
                pair = tuple(sorted([node.id, iface.to]))
                if pair in seen:
                    continue
                seen.add(pair)
                a_idx = iface_index[(node.id, iface.to)]
                b_idx = iface_index[(iface.to, node.id)]
                topology["links"].append(
                    {"endpoints": [f"{node.id}:eth{a_idx}", f"{iface.to}:eth{b_idx}"]}
                )
        return {"name": scenario.name, "topology": topology}

    def _clab(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sudo", "-n", CONTAINERLAB_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _docker_exec(self, container: str, cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "exec", container, *cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _wait_for_firewalld(self, container: str, timeout: int = 30) -> bool:
        for _ in range(timeout * 2):
            r = self._docker_exec(container, ["firewall-cmd", "--state"], timeout=5)
            if r.returncode == 0 and "running" in r.stdout:
                return True
            time.sleep(0.5)
        return False

    def _configure_node(self, scenario_name: str, scenario: Scenario) -> list[str]:
        warnings: list[str] = []
        for node in scenario.nodes:
            container = f"clab-{scenario_name}-{node.id}"
            if node.role == "firewall":
                if not self._wait_for_firewalld(container):
                    warnings.append(f"{container}: firewalld did not become ready in time")
            for idx, iface in enumerate(node.interfaces, start=1):
                eth = f"eth{idx}"
                r1 = self._docker_exec(container, ["ip", "addr", "add", iface.ip, "dev", eth])
                if r1.returncode != 0 and "File exists" not in r1.stderr:
                    warnings.append(f"{container}: ip addr add {iface.ip} dev {eth} -> {r1.stderr.strip()}")
                r2 = self._docker_exec(container, ["ip", "link", "set", eth, "up"])
                if r2.returncode != 0:
                    warnings.append(f"{container}: ip link set {eth} up -> {r2.stderr.strip()}")
                if iface.gateway:
                    r3 = self._docker_exec(
                        container, ["ip", "route", "replace", "default", "via", iface.gateway]
                    )
                    if r3.returncode != 0:
                        warnings.append(
                            f"{container}: ip route replace default via {iface.gateway} -> {r3.stderr.strip()}"
                        )
        return warnings

    def get_mgmt_ip(self, scenario_name: str, node_id: str) -> str | None:
        container = f"clab-{scenario_name}-{node_id}"
        result = subprocess.run(
            ["docker", "inspect", container, "--format",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def start(self, scenario: Scenario) -> dict:
        topology_file = self._topology_file(scenario.name)
        if topology_file.exists():
            inspect = self._clab(
                ["inspect", "-t", str(topology_file), "--format", "json"], timeout=10
            )
            if inspect.returncode == 0:
                raise LabAlreadyRunning(
                    f"Lab '{scenario.name}' is already running. Stop it first."
                )
        topology_file.write_text(
            yaml.safe_dump(self._to_containerlab(scenario), sort_keys=False)
        )
        result = self._clab(["deploy", "-t", str(topology_file)], timeout=180)
        if result.returncode != 0:
            raise RuntimeError(
                f"containerlab deploy failed (exit {result.returncode}):\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        warnings = self._configure_node(scenario.name, scenario)
        nodes = [
            {"id": n.id, "role": n.role, "container": f"clab-{scenario.name}-{n.id}"}
            for n in scenario.nodes
        ]
        return {
            "status": "started",
            "scenario": scenario.name,
            "nodes": nodes,
            "warnings": warnings,
        }

    def stop(self, scenario_name: str) -> None:
        topology_file = self._topology_file(scenario_name)
        if not topology_file.exists():
            raise LabNotFound(
                f"No topology file for scenario '{scenario_name}' at {topology_file}"
            )
        result = self._clab(
            ["destroy", "-t", str(topology_file), "--cleanup"], timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"containerlab destroy failed (exit {result.returncode}):\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

    def state(self) -> dict:
        result = self._clab(["inspect", "--all", "--format", "json"], timeout=10)
        if result.returncode != 0:
            return {"labs": [], "note": "no labs deployed or inspect failed"}
        try:
            return json.loads(result.stdout) if result.stdout.strip() else {"labs": []}
        except json.JSONDecodeError:
            return {"raw": result.stdout, "note": "stdout was not valid JSON"}

    def exec(self, scenario_name: str, node_id: str, cmd: list[str]) -> dict:
        container = f"clab-{scenario_name}-{node_id}"
        result = self._docker_exec(container, cmd, timeout=30)
        return {
            "container": container,
            "cmd": cmd,
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
