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
