import re
from typing import Protocol

import httpx


class FirewallDriver(Protocol):
    def block(self, src_ip: str | None, dst_ip: str | None, proto: str) -> dict: ...
    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str) -> dict: ...
    def list_rules(self) -> dict: ...
    def flush(self) -> dict: ...
    def health(self) -> dict: ...


# Matches rich-rule strings produced by firewall-image/fw-api.py:_build_rich_rule.
# Anchored optional groups so order is fixed:
#   rule family="ipv4" [source address="..."] [destination address="..."]
#     [icmp-type name="..." | port port="..." protocol="..." | protocol value="..."] (drop|accept|reject)
_RULE_SRC = re.compile(r'source address="([^"]+)"')
_RULE_DST = re.compile(r'destination address="([^"]+)"')
_RULE_ICMP = re.compile(r'icmp-type name="([^"]+)"')
_RULE_PORT = re.compile(r'port port="([^"]+)" protocol="(tcp|udp)"')
_RULE_PROTO_ONLY = re.compile(r'protocol value="(tcp|udp)"')
_RULE_ACTION = re.compile(r'\b(drop|accept|reject)\b\s*$')


def parse_rich_rule(rule_str: str) -> dict | None:
    """Parse a firewalld rich-rule string into structured fields.

    Returns None if no recognisable action is present.
    """
    rule_str = rule_str.strip()
    if not rule_str:
        return None
    action_m = _RULE_ACTION.search(rule_str)
    if not action_m:
        return None
    src_m = _RULE_SRC.search(rule_str)
    dst_m = _RULE_DST.search(rule_str)

    proto: str | None = None
    port: str | None = None
    if _RULE_ICMP.search(rule_str):
        proto = "icmp"
    else:
        port_m = _RULE_PORT.search(rule_str)
        if port_m:
            port = port_m.group(1)
            proto = port_m.group(2)
        else:
            proto_m = _RULE_PROTO_ONLY.search(rule_str)
            if proto_m:
                proto = proto_m.group(1)

    return {
        "src_ip": src_m.group(1) if src_m else None,
        "dst_ip": dst_m.group(1) if dst_m else None,
        "proto": proto,
        "port": port,
        "action": action_m.group(1),
        "raw": rule_str,
    }


class FirewalldDriver:
    def __init__(self, mgmt_url: str):
        self.mgmt_url = mgmt_url.rstrip("/")
        self._client = httpx.Client(timeout=10.0)

    def _post(self, path: str, json_body: dict | None = None) -> dict:
        resp = self._client.post(f"{self.mgmt_url}{path}", json=json_body)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._client.get(f"{self.mgmt_url}{path}")
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, json_body: dict | None = None) -> dict:
        resp = self._client.request("DELETE", f"{self.mgmt_url}{path}", json=json_body)
        resp.raise_for_status()
        return resp.json()

    def block(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp") -> dict:
        return self._post("/rules", {
            "src_ip": src_ip, "dst_ip": dst_ip, "protocol": proto, "action": "drop",
        })

    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp") -> dict:
        # firewalld rich rules are first-match-wins at equal priority — a prior DROP
        # would shadow this ACCEPT, so the DROP must actually be removed.
        #
        # We deliberately ignore `proto` when removing and instead clear EVERY drop
        # whose src/dst pair matches. Re-enabling a connection means "unblock these
        # two hosts", and the LLM frequently passes the wrong proto on re-enable
        # (e.g. "all" instead of "icmp"); a proto-exact delete then matches nothing
        # and leaves the real drop in place. Reconstructing each delete from the
        # PARSED rule guarantees the rich-rule string matches what was stored,
        # because add and remove both go through fw-api's _build_rich_rule.
        removed: list[dict] = []
        try:
            for rule in self.list_rules().get("parsed", []):
                if rule.get("action") != "drop":
                    continue
                # Match the pair in EITHER direction: "allow traffic between A and
                # B" should clear the block regardless of which way the LLM (or the
                # original block) ordered src/dst. A direction swap otherwise leaves
                # the real drop in place.
                if {rule.get("src_ip"), rule.get("dst_ip")} != {src_ip, dst_ip}:
                    continue
                # proto None == a protocol-less drop (a "proto: all" block); passing
                # "all" makes _build_rich_rule emit no protocol clause, matching it.
                del_proto = rule.get("proto") or "all"
                try:
                    r = self._delete("/rules", {
                        "src_ip": rule.get("src_ip"),
                        "dst_ip": rule.get("dst_ip"),
                        "protocol": del_proto,
                        "port": rule.get("port"),
                        "action": "drop",
                    })
                    removed.append({"rule": rule.get("raw"), "result": r})
                except httpx.HTTPStatusError as e:
                    removed.append({"rule": rule.get("raw"), "error": str(e)})
        except Exception as e:
            removed.append({"error": f"list/remove failed: {e}"})
        added = self._post("/rules", {
            "src_ip": src_ip, "dst_ip": dst_ip, "protocol": proto, "action": "accept",
        })
        return {"removed_drops": removed, "added_accept": added}

    def list_rules(self) -> dict:
        raw = self._get("/rules")
        all_rules = list(raw.get("forward_rules", [])) + list(raw.get("zone_rules", []))
        parsed = [p for p in (parse_rich_rule(r) for r in all_rules) if p is not None]
        raw["parsed"] = parsed
        return raw

    def flush(self) -> dict:
        return self._post("/flush")

    def health(self) -> dict:
        return self._get("/health")
