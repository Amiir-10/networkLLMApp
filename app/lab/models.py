from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

NodeRole = Literal["pc", "firewall", "router", "switch"]


class Interface(BaseModel):
    to: str
    # Optional: a switch's data ports carry no L3 address (pure L2 bridge member).
    # Required in practice for every other role — enforced by Node.ip_required_off_switch.
    ip: str | None = Field(default=None, pattern=r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
    gateway: str | None = Field(default=None, pattern=r"^\d{1,3}(\.\d{1,3}){3}$")


class Route(BaseModel):
    """A static route applied inside a node: `ip route replace <to> via <via>`.
    Needed for multi-subnet topologies where a router/firewall must reach a
    non-adjacent subnet through a specific next-hop (the single per-interface
    `gateway` default route is not enough)."""
    to: str = Field(..., pattern=r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
    via: str = Field(..., pattern=r"^\d{1,3}(\.\d{1,3}){3}$")


class Node(BaseModel):
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    role: NodeRole
    image: str = "alpine:3.20"
    interfaces: list[Interface]
    # Extra static routes (beyond the per-interface default gateway). Used by
    # routers/firewalls in multi-subnet scenarios; applied after L3 iface config.
    routes: list[Route] = Field(default_factory=list)

    @model_validator(mode="after")
    def ip_required_off_switch(self) -> "Node":
        # Only a switch may have IP-less (pure L2) interfaces.
        if self.role != "switch":
            for iface in self.interfaces:
                if not iface.ip:
                    raise ValueError(
                        f"node '{self.id}' ({self.role}) interface to '{iface.to}' "
                        f"requires an ip (only switches may be L2-only)"
                    )
        return self
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
