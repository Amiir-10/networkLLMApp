from typing import Protocol

import httpx


class FirewallDriver(Protocol):
    def block(self, src_ip: str | None, dst_ip: str | None, proto: str) -> dict: ...
    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str) -> dict: ...
    def list_rules(self) -> dict: ...
    def flush(self) -> dict: ...
    def health(self) -> dict: ...


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

    def block(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp") -> dict:
        return self._post("/rules", {
            "src_ip": src_ip, "dst_ip": dst_ip, "protocol": proto, "action": "drop",
        })

    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp") -> dict:
        return self._post("/rules", {
            "src_ip": src_ip, "dst_ip": dst_ip, "protocol": proto, "action": "accept",
        })

    def list_rules(self) -> dict:
        return self._get("/rules")

    def flush(self) -> dict:
        return self._post("/flush")

    def health(self) -> dict:
        return self._get("/health")
