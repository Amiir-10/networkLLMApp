"""Topology engine: containerlab lifecycle (deploy/destroy/state/exec).

This is the relocated ContainerlabLabDriver. Class renamed TopologyEngine to
match the engine layer; method signatures (start/stop/state/exec/get_mgmt_ip)
are unchanged so callers re-point with no behaviour change. Per-node L3 config
is delegated to app.engines.netconfig (the network-config engine).

Future CRUD verbs (add_node / remove_node / isolate_node) land here.
"""
import json
import subprocess
from pathlib import Path
from typing import Protocol

import yaml

from app.lab.models import Scenario
from app.engines import netconfig

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


class TopologyEngine:
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
            # IPv6 disabled on every node regardless of image (standing rule),
            # stamped at creation. Merge with the firewall's ip_forward.
            sysctls = dict(netconfig.IPV6_DISABLE_SYSCTLS)
            clab_node: dict = {
                "kind": "linux",
                "image": node.image,
                "sysctls": sysctls,
            }
            # Firewalls AND routers forward IPv4 (routers are L3 transit between
            # subnets; the firewall already did). Switches are L2 — no forwarding.
            if node.role in ("firewall", "router"):
                sysctls["net.ipv4.ip_forward"] = 1
            # Keep a bare host alive with sleep infinity, UNLESS its image runs a
            # service as PID 1 (idle=False: nginx, postgres) or it's the firewall
            # (firewalld is its PID 1). Routers/switches are idle alpine → kept alive.
            if node.role != "firewall" and node.idle:
                clab_node["cmd"] = "sleep infinity"
            if node.env:
                clab_node["env"] = dict(node.env)
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
        warnings = netconfig.configure_nodes(scenario.name, scenario)
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
        result = netconfig.docker_exec(container, cmd, timeout=30)
        return {
            "container": container,
            "cmd": cmd,
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
