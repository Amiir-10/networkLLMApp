"""Security engine: the single firewall + scan surface.

Both the LLM (app.chat.dispatch_tool) and the UI/console (app.main, and the
future console rule panel) call THIS object — never FirewalldDriver directly —
so the block/allow/list/flush/scan logic can never drift between the two paths.

FirewalldDriver's rule logic (esp. the fragile allow() drop-clearing) is wrapped
unchanged; this engine only adds the connect/disconnect lifecycle that main.py
previously did by reassigning a module global.
"""
from app.firewall.driver import FirewalldDriver
from app.scanner import run_image_scan


class SecurityEngine:
    def __init__(self) -> None:
        self._fw: FirewalldDriver | None = None

    def connect(self, mgmt_url: str) -> None:
        """Bind to a firewall container's REST API (called after lab deploy,
        once the mgmt IP is known). Mirrors the old `_firewall_driver = ...`."""
        self._fw = FirewalldDriver(mgmt_url=mgmt_url)

    def disconnect(self) -> None:
        """Drop the binding (called on lab stop). Mirrors `_firewall_driver = None`."""
        self._fw = None

    @property
    def connected(self) -> bool:
        return self._fw is not None

    # ── firewall rule CRUD (delegated, logic unchanged) ──────────────────
    def health(self) -> dict:
        return self._fw.health()

    def block(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp",
              port: int | str | None = None) -> dict:
        return self._fw.block(src_ip, dst_ip, proto, port)

    def allow(self, src_ip: str | None, dst_ip: str | None, proto: str = "icmp") -> dict:
        return self._fw.allow(src_ip, dst_ip, proto)

    def list_rules(self) -> dict:
        return self._fw.list_rules()

    def flush(self) -> dict:
        return self._fw.flush()

    # ── image vulnerability scan ─────────────────────────────────────────
    def scan(self, image: str) -> dict:
        return run_image_scan(image)
