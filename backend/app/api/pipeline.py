"""Pipeline, agent status, resolution guide downloads, integrations, notifications."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..context import AppContext
from ..pipeline import ORDER, STAGES, start_pipeline
from ..pipeline.common import State, get_incident
from ..pipeline.notify_doc import stage_servicenow
from ..services import activity, guide
from ..services import integrations as integ
from ..services.notify import validate_webhook
from .deps import get_ctx, require

router = APIRouter(prefix="/api")
NAMES = {a["id"]: a["name"] for a in activity.AGENTS}


@router.get("/agents")
def agents(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    items = activity.agent_status(ctx)
    runs = ctx.db.query("SELECT r.id,r.incident_id,r.status,r.trigger,r.created_at,i.title FROM pipeline_runs r "
                        "LEFT JOIN incidents i ON i.id=r.incident_id WHERE r.status='running' ORDER BY r.id DESC LIMIT 10")
    counts = {"running": 0, "idle": 0, "error": 0, "not_configured": 0, "disabled": 0, "stopped": 0}
    for a in items:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    llm = ctx.llm.safe_get().describe()
    return {"agents": items, "running_pipelines": runs, "counts": counts, "llm": llm}


@router.get("/agents/activity")
def agent_activity(agent: str = "", limit: int = 50, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    sql, p = "SELECT id,agent,run_id,incident_id,status,started_at,finished_at,ms,summary,error,provider,model,created_by FROM agent_activity", []
    if agent:
        sql += " WHERE agent=?"
        p.append(agent)
    rows = ctx.db.query(sql + " ORDER BY id DESC LIMIT ?", (*p, max(1, min(limit, 200))))
    for r in rows:
        r["agent_name"] = NAMES.get(r["agent"], r["agent"])
    return rows


@router.post("/incidents/{iid}/pipeline", status_code=202)
def run_incident_pipeline(iid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    if not ctx.db.one("SELECT 1 FROM incidents WHERE id=?", (iid,)):
        raise HTTPException(404, "Incident not found")
    run = start_pipeline(ctx, iid, user["username"], "manual")
    return {"run_id": run, "status": "running"}


def _run_view(ctx: AppContext, run: dict) -> dict:
    rows = ctx.db.query("SELECT * FROM agent_activity WHERE run_id=? ORDER BY id", (run["id"],))
    by_agent = {r["agent"]: r for r in rows}
    stages = []
    for sid in ORDER:
        r = by_agent.get(sid)
        stages.append({"agent": sid, "name": NAMES[sid], "status": r["status"] if r else "pending",
                       "summary": r["summary"] if r else "", "error": r["error"] if r else None, "ms": r["ms"] if r else None,
                       "result": json.loads(r["result"]) if r and r["result"] else None,
                       "provider": r["provider"] if r else None, "started_at": r["started_at"] if r else None})
    return {**run, "stages": stages}


@router.get("/incidents/{iid}/pipeline")
def incident_pipeline(iid: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    inc = ctx.db.one("SELECT id,title,priority,score,category,duplicate_of,eta_min,eta_max,itsm_ref,itsm_url,status,resolved_at FROM incidents WHERE id=?", (iid,))
    if not inc:
        raise HTTPException(404, "Incident not found")
    run = ctx.db.one("SELECT * FROM pipeline_runs WHERE incident_id=? ORDER BY id DESC LIMIT 1", (iid,))
    notes = ctx.db.query("SELECT channel,status,detail,created_at FROM notifications WHERE incident_id=? ORDER BY id DESC LIMIT 20", (iid,))
    return {"incident": inc, "run": _run_view(ctx, run) if run else None, "notifications": notes}


def _latest_guide(ctx: AppContext, iid: int) -> str:
    r = ctx.db.one("SELECT result FROM agent_activity WHERE incident_id=? AND agent='documenter' AND status='ok' ORDER BY id DESC LIMIT 1", (iid,))
    if not r or not r["result"]:
        raise HTTPException(404, "No resolution guide yet. Run the pipeline for this incident first.")
    return json.loads(r["result"])["markdown"]


@router.get("/incidents/{iid}/resolution.{fmt}")
def resolution(iid: int, fmt: str, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    if fmt not in ("pdf", "txt", "md"):
        raise HTTPException(404, "Format must be pdf, txt or md")
    md = _latest_guide(ctx, iid)
    name = f"incident-{iid}-resolution-guide.{fmt}"
    hdr = {"Content-Disposition": f'attachment; filename="{name}"'}
    if fmt == "pdf":
        return Response(guide.to_pdf(md, f"Incident {iid} resolution guide"), media_type="application/pdf", headers=hdr)
    if fmt == "txt":
        return Response(guide.to_text(md), media_type="text/plain; charset=utf-8", headers=hdr)
    return Response(md, media_type="text/markdown; charset=utf-8", headers=hdr)


@router.post("/incidents/{iid}/ticket")
def create_ticket(iid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    """Create (or report the existing) ServiceNow ticket for this incident, without rerunning the whole pipeline."""
    inc = get_incident(ctx, iid) if ctx.db.one("SELECT 1 FROM incidents WHERE id=?", (iid,)) else None
    if not inc:
        raise HTTPException(404, "Incident not found")
    st = State(run_id=0, incident_id=iid, user=user["username"], incident=inc)
    g = ctx.db.one("SELECT result FROM agent_activity WHERE incident_id=? AND agent='documenter' AND status='ok' ORDER BY id DESC LIMIT 1", (iid,))
    if g and g["result"]:
        st.outputs["documenter"] = json.loads(g["result"])
    with activity.Tracked(ctx, "servicenow", incident_id=iid, user=user["username"]) as t:
        try:
            out = stage_servicenow(ctx, st)
        except Exception as e:  # noqa: BLE001
            t.fail(f"{type(e).__name__}: {e}")
            raise HTTPException(502, f"ServiceNow: {e}")
        t.ok(out.summary, out.result, status=out.status)
    return {"status": out.status, "summary": out.summary, **out.result}


# ---------- integrations ----------
class IntegrationIn(BaseModel):
    enabled: bool | None = None
    min_priority: str | None = Field(default=None, pattern="^P[1-4]$")
    url: str | None = Field(default=None, max_length=600)
    routing_key: str | None = Field(default=None, max_length=200)
    host: str | None = Field(default=None, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=300)
    from_addr: str | None = Field(default=None, max_length=200)
    to: list[str] | str | None = None
    starttls: bool | None = None
    instance_url: str | None = Field(default=None, max_length=300)


@router.get("/integrations")
def get_integrations(_: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    return integ.get_integrations(ctx)


@router.put("/integrations/{name}")
def put_integration(name: str, body: IntegrationIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    try:
        res = integ.update_integration(ctx, name, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    ctx.db.audit(admin["username"], "integration.update", name, {k: ("***" if k in ("password", "routing_key", "url") else v) for k, v in body.model_dump(exclude_none=True).items()})
    return res


@router.post("/integrations/{name}/test")
def test_integration(name: str, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    from ..services import notify as N
    cfg = integ.get_secrets(ctx)
    if name not in cfg:
        raise HTTPException(404, "Unknown integration")
    c = cfg[name]
    fake = {"id": 0, "title": "LogFix test notification", "severity": "INFO", "event_count": 0, "priority": "P4", "fingerprint": "test", "service": "logfix"}
    try:
        if name == "slack":
            if not c["url"]:
                raise RuntimeError("no webhook URL saved")
            detail = N.send_slack(c["url"], fake, "This is a test from LogFix AI.")
        elif name == "pagerduty":
            if not c["routing_key"]:
                raise RuntimeError("no routing key saved")
            fake["priority"] = "P4"
            detail = N.send_pagerduty(c["routing_key"], {**fake, "title": "LogFix test event (safe to resolve)"})
        elif name == "email":
            detail = N.send_email(c, fake, "This is a test from LogFix AI.")
        else:
            if not (c["instance_url"] and c["username"] and c["password"]):
                raise RuntimeError("instance URL, username and password are required")
            import httpx
            r = httpx.get(c["instance_url"].rstrip("/") + "/api/now/table/incident?sysparm_limit=1", auth=(c["username"], c["password"]),
                          headers={"Accept": "application/json"}, timeout=15)
            if r.status_code >= 300:
                raise RuntimeError(f"HTTP {r.status_code}")
            detail = "connected (read check only; no ticket created)"
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "detail": detail}


@router.get("/notifications")
def notifications(limit: int = 50, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return ctx.db.query("SELECT n.*, i.title FROM notifications n LEFT JOIN incidents i ON i.id=n.incident_id ORDER BY n.id DESC LIMIT ?",
                        (max(1, min(limit, 200)),))
