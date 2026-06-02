"""Security engine: the firewall(s) + scan surface.

Both the LLM (app.chat.dispatch_tool) and the UI/console (app.main, the console
rule panel) call THIS object — never FirewalldDriver directly — so the
block/allow/list/flush/scan logic can never drift between the two paths.

Supports ONE OR MORE firewalls, keyed by firewall node id. A single-firewall
scenario (central-hub) is unchanged: every rule method takes an optional `fw_id`
that **defaults to the sole firewall when exactly one is connected**, so existing
callers that pass no fw_id behave identically. A multi-firewall scenario
(two-subnet-ixp) resolves the target firewall deterministically in the backend
(see app.chat.resolve_firewall) — the model never has to pick one.

Aggregation: `list_rules()` with no fw_id merges every firewall's rules and tags
each parsed rule with its `firewall` id; `flush()` with no fw_id clears them all.
FirewalldDriver's rule logic (esp. the fragile allow() drop-clearing) is wrapped
unchanged.
"""
from app.firewall.driver import FirewalldDriver
from app.scanner import run_image_scan


class SecurityEngine:
    def __init__(self) -> None:
        self._fws: dict[str, FirewalldDriver] = {}

    def connect(self, fw_id: str, mgmt_url: str) -> None:
        """Bind a firewall container's REST API under its node id (called per
        firewall node after lab deploy, once each mgmt IP is known)."""
        self._fws[fw_id] = FirewalldDriver(mgmt_url=mgmt_url)

    def disconnect(self) -> None:
        """Drop every binding (called on lab stop/reset)."""
        self._fws = {}

    @property
    def connected(self) -> bool:
        return len(self._fws) > 0

    def firewall_ids(self) -> list[str]:
        return list(self._fws.keys())

    def _resolve(self, fw_id: str | None) -> tuple[str, FirewalldDriver]:
        """Map an optional fw_id to a concrete (id, driver). None + exactly one
        firewall = that firewall (the central-hub case). None + several = error
        (callers must resolve a target first)."""
        if not self._fws:
            raise RuntimeError("No firewall connected")
        if fw_id is not None:
            fw = self._fws.get(fw_id)
            if fw is None:
                raise RuntimeError(
                    f"Unknown firewall '{fw_id}'. Connected: {self.firewall_ids()}"
                )
            return fw_id, fw
        if len(self._fws) == 1:
            (only_id, only_fw), = self._fws.items()
            return only_id, only_fw
        raise RuntimeError(
            f"Ambiguous firewall (connected: {self.firewall_ids()}); specify fw_id"
        )

    # ── firewall rule CRUD (delegated, logic unchanged) ──────────────────
    def health(self, fw_id: str | None = None) -> dict:
        _, fw = self._resolve(fw_id)
        return fw.health()

    def block(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp",
              port: int | str | None = None, fw_id: str | None = None) -> dict:
        _, fw = self._resolve(fw_id)
        return fw.block(src_ip, dst_ip, proto, port)

    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp",
              fw_id: str | None = None) -> dict:
        _, fw = self._resolve(fw_id)
        return fw.allow(src_ip, dst_ip, proto)

    def flush(self, fw_id: str | None = None) -> dict:
        """fw_id given = flush that firewall; None = flush EVERY firewall (the
        flush_rules "clear everything" intent)."""
        if fw_id is None:
            return {"flushed": {fid: fw.flush() for fid, fw in self._fws.items()}}
        _, fw = self._resolve(fw_id)
        return fw.flush()

    def list_rules(self, fw_id: str | None = None) -> dict:
        """fw_id given = that firewall's rules; None = aggregate across all
        firewalls. Either way each parsed rule is tagged with its `firewall` id
        (extra field; harmless to single-firewall consumers)."""
        if fw_id is not None:
            fid, fw = self._resolve(fw_id)
            return self._tagged(fid, fw.list_rules())
        forward: list = []
        zone: list = []
        parsed: list = []
        for fid, fw in self._fws.items():
            r = fw.list_rules()
            forward += r.get("forward_rules", [])
            zone += r.get("zone_rules", [])
            parsed += [{**p, "firewall": fid} for p in r.get("parsed", [])]
        return {"forward_rules": forward, "zone_rules": zone, "parsed": parsed}

    @staticmethod
    def _tagged(fid: str, r: dict) -> dict:
        r = dict(r)
        r["parsed"] = [{**p, "firewall": fid} for p in r.get("parsed", [])]
        return r

    # ── image vulnerability scan ─────────────────────────────────────────
    def scan(self, image: str) -> dict:
        return run_image_scan(image)

    def scan_images(self, images: list[str]) -> dict:
        """Scan one or more images, deduping identical images first: the scan
        target is the IMAGE, so two nodes on the same image (e.g. pc1/pc2 both
        nginx:alpine) are scanned ONCE — this is the multi-scan efficiency win.
        Returns a per-image scan list plus a severity total aggregated across
        every SUCCESSFUL scan (an errored/unpullable image is reported in its
        own scan entry but skipped in the total, so one bad image can't break
        the batch)."""
        seen: list[str] = []
        for img in images:
            if img not in seen:
                seen.append(img)
        scans = [run_image_scan(img) for img in seen]
        total: dict[str, int] = {}
        for s in scans:
            if "error" in s:
                continue
            for sev, n in (s.get("by_severity") or {}).items():
                total[sev] = total.get(sev, 0) + n
        return {"images_scanned": len(seen), "scans": scans, "by_severity_total": total}
