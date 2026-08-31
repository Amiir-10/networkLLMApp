"""Container-image vulnerability scanning via dockerscan (cr0hn/dockerscan).

dockerscan analyses a Docker *image* (CVEs, CIS Docker Benchmark, hardcoded
secrets, supply-chain/runtime issues) — it is NOT a network/service scanner.
So a node's scan target is the image that node runs, not its live services.

Requires the dockerscan binary and its CVE database:
    curl -L .../dockerscan-linux-amd64 -o ~/.local/bin/dockerscan && chmod +x ...
    dockerscan update-db
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

DOCKERSCAN_BIN = os.environ.get(
    "DOCKERSCAN_BIN", os.path.expanduser("~/.local/bin/dockerscan")
)


def _scan_env() -> dict:
    """Environment for the dockerscan subprocess.

    dockerscan reads its CVE database from `~/.dockerscan/cve-db.sqlite` via
    Go's os.UserHomeDir(), which FAILS with "user home directory not defined"
    when HOME is unset — exactly what happens under systemd (the showcase
    backend). Derive HOME from the binary's own location (`.../.local/bin/
    dockerscan` → the home dir two levels up) so the scan works whether or not
    the service manager set HOME."""
    env = dict(os.environ)
    if not env.get("HOME"):
        # ~/.local/bin/dockerscan -> parents[2] == ~
        try:
            env["HOME"] = str(Path(DOCKERSCAN_BIN).resolve().parents[2])
        except IndexError:
            env["HOME"] = "/root"
    return env

# Severity ordering so the summary surfaces the worst findings first.
_SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def run_image_scan(image: str, timeout: int = 180) -> dict:
    """Scan a local Docker image and return a compact, LLM-friendly summary."""
    if not Path(DOCKERSCAN_BIN).exists():
        return {
            "error": f"dockerscan not found at {DOCKERSCAN_BIN}. "
            "Install it and run 'dockerscan update-db'.",
            "image": image,
        }

    with tempfile.TemporaryDirectory() as tmp:
        # dockerscan writes dockerscan-report.{json,sarif} into its CWD (the
        # --output flag does not relocate the file), so run it inside tmp to
        # keep the repo clean and find the report deterministically.
        cmd = [DOCKERSCAN_BIN, "-q", image]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=tmp,
                env=_scan_env(),
            )
        except subprocess.TimeoutExpired:
            return {"error": f"scan timed out after {timeout}s", "image": image}

        report = Path(tmp) / "dockerscan-report.json"
        if not report.exists():
            return {
                "error": "dockerscan produced no JSON report",
                "image": image,
                "stderr": proc.stderr[-400:],
                "stdout": proc.stdout[-400:],
            }
        try:
            data = json.loads(report.read_text())
        except json.JSONDecodeError as e:
            return {"error": f"could not parse scan report: {e}", "image": image}

    return _summarize(image, data)


def _summarize(image: str, data: dict) -> dict:
    summary = data.get("summary", {}) or {}
    findings = data.get("findings", []) or []
    by_severity = summary.get("by_severity", {}) or {}

    top = sorted(findings, key=lambda f: _SEV_ORDER.get(f.get("severity", ""), 9))[:8]
    top_findings = [
        {
            "id": f.get("id"),
            "severity": f.get("severity"),
            "category": f.get("category"),
            "title": f.get("title"),
            "remediation": f.get("remediation"),
        }
        for f in top
    ]

    return {
        "image": image,
        "total_findings": summary.get("total_findings", len(findings)),
        "by_severity": by_severity,
        "by_category": summary.get("by_category", {}) or {},
        "top_findings": top_findings,
    }
