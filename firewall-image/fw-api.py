import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="firewalld-api", version="0.2.0")

POLICY = "fwd-filter"


def _run(cmd: list[str], timeout: int = 10) -> dict:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "exit": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


class RuleRequest(BaseModel):
    src_ip: str | None = None
    dst_ip: str | None = None
    protocol: str = "icmp"
    action: str = "drop"
    port: str | None = None


def _build_rich_rule(req: RuleRequest) -> str:
    rule = 'rule family="ipv4"'
    if req.src_ip:
        rule += f' source address="{req.src_ip}"'
    if req.dst_ip:
        rule += f' destination address="{req.dst_ip}"'
    if req.protocol == "icmp":
        rule += ' icmp-type name="echo-request"'
    elif req.protocol in ("tcp", "udp") and req.port:
        rule += f' port port="{req.port}" protocol="{req.protocol}"'
    elif req.protocol in ("tcp", "udp"):
        rule += f' protocol value="{req.protocol}"'
    rule += f" {req.action}"
    return rule


@app.get("/health")
def health():
    result = _run(["firewall-cmd", "--state"])
    running = result["stdout"] == "running"
    return {"status": "ok" if running else "degraded", "firewalld": result["stdout"]}


@app.get("/rules")
def list_rules():
    fwd = _run(["firewall-cmd", f"--policy={POLICY}", "--list-rich-rules"])
    zone = _run(["firewall-cmd", "--zone=public", "--list-rich-rules"])
    return {
        "forward_rules": fwd["stdout"].splitlines() if fwd["stdout"] else [],
        "zone_rules": zone["stdout"].splitlines() if zone["stdout"] else [],
    }


@app.post("/rules")
def add_rule(req: RuleRequest):
    rule = _build_rich_rule(req)
    result = _run(["firewall-cmd", f"--policy={POLICY}", "--add-rich-rule", rule])
    if result["exit"] != 0:
        raise HTTPException(status_code=500, detail=f"{result['stderr']} (rule: {rule})")
    return {"status": "applied", "rule": rule, "detail": result["stdout"]}


@app.delete("/rules")
def remove_rule(req: RuleRequest):
    rule = _build_rich_rule(req)
    result = _run(["firewall-cmd", f"--policy={POLICY}", "--remove-rich-rule", rule])
    if result["exit"] != 0:
        raise HTTPException(status_code=500, detail=result["stderr"])
    return {"status": "removed", "rule": rule}


@app.post("/flush")
def flush_rules():
    fwd = _run(["firewall-cmd", f"--policy={POLICY}", "--list-rich-rules"])
    removed = []
    for rule in fwd["stdout"].splitlines():
        if rule.strip():
            r = _run(["firewall-cmd", f"--policy={POLICY}", "--remove-rich-rule", rule.strip()])
            removed.append({"rule": rule.strip(), "result": r["stdout"]})
    return {"status": "flushed", "removed_count": len(removed), "removed": removed}


@app.get("/zones")
def list_zones():
    result = _run(["firewall-cmd", "--get-active-zones"])
    detail = _run(["firewall-cmd", "--zone=public", "--list-all"])
    policy = _run(["firewall-cmd", f"--info-policy={POLICY}"])
    return {
        "active_zones": result["stdout"],
        "public_zone": detail["stdout"],
        "forward_policy": policy["stdout"],
    }
