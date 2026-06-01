from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

NodeRole = Literal["pc", "firewall", "router", "switch"]


class Interface(BaseModel):
    to: str
    ip: str = Field(..., pattern=r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
    gateway: str | None = Field(default=None, pattern=r"^\d{1,3}(\.\d{1,3}){3}$")


class Node(BaseModel):
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    role: NodeRole
    image: str = "alpine:3.20"
    interfaces: list[Interface]
    # --- service / runtime (Phase 3b: real PC services) ---
    # `idle=True` keeps an otherwise-empty host alive with `sleep infinity` (the
    # right default for a bare alpine box). Set `idle=False` when the image runs
    # its own service as PID 1 (nginx, postgres, …) so its entrypoint isn't
    # shadowed. `env` is passed straight to the containerlab node (e.g. postgres
    # auth). `launch` is a shell command started DETACHED inside the container
    # AFTER L3 config (generalises the old :8080 listener — for hosts that need a
    # service started by hand rather than via the image entrypoint). `ports` is
    # the declared set of listening ports (display + the Phase-3b port-aware
    # firewall surface); it does not itself open anything.
    idle: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    launch: str | None = None
    ports: list[int] = Field(default_factory=list)


class Scenario(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    description: str = ""
    nodes: list[Node]

    @field_validator("nodes")
    @classmethod
    def unique_node_ids(cls, nodes: list[Node]) -> list[Node]:
        ids = [n.id for n in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        return nodes

    @model_validator(mode="after")
    def links_are_symmetric(self) -> "Scenario":
        node_ids = {n.id for n in self.nodes}
        for node in self.nodes:
            for iface in node.interfaces:
                if iface.to not in node_ids:
                    raise ValueError(f"node '{node.id}' has interface to unknown node '{iface.to}'")
                peer = next(n for n in self.nodes if n.id == iface.to)
                if not any(p.to == node.id for p in peer.interfaces):
                    raise ValueError(
                        f"link asymmetry: '{node.id}' declares interface to '{iface.to}', "
                        f"but '{iface.to}' has no interface back to '{node.id}'"
                    )
        return self
