"""Sources, logs, incidents, investigations, overview."""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..agents.investigator import start_investigation
from ..connectors.base import ConnectorError
from ..connectors.demo import SCENARIOS
from ..context import AppContext
from ..db import now_iso, parse_ts, to_iso, utcnow
from ..normalize import SEVERITIES
from ..services.collector import collect_source
from ..services.reports import incident_report_html
from .deps import current_user, get_ctx, require

router = APIRouter(prefix="/api")

SECRET_FIELDS = {"aws": ["access_key_id", "secret_access_key", "session_token"],
                 "azure": ["client_secret"], "gcp": ["service_account_json"], "demo": []}


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cloud: str = Field(pattern="^(aws|azure|gcp|demo)$")
    config: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)
    interval_seconds: int = Field(default=0)
    enabled: bool = True


class SourcePatch(BaseModel):
    name: str | None = None
    config: dict | None = None
    secrets: dict | None = None
    interval_seconds: int | None = None
    enabled: bool | None = None


def _check_interval(v: int) -> None:
    if v != 0 and not (30 <= v <= 86400):
        raise HTTPException(400, "interval_seconds must be 0 (manual) or between 30 and 86400")


def _pub_source(s: dict, ctx: AppContext) -> dict:
    sec = ctx.secrets.decrypt(s.get("secrets_enc"))
    return {"id": s["id"], "name": s["name"], "cloud": s["cloud"], "config": json.loads(s["config"] or "{}"),
            "secrets_set": sorted(sec.keys()), "interval_seconds": s["interval_seconds"], "enabled": bool(s["enabled"]),
            "last_collected_at": s["last_collected_at"], "last_status": s["last_status"], "last_error": s["last_error"]}


def _clean_secrets(cloud: str, secrets: dict) -> dict:
    allowed = SECRET_FIELDS[cloud]
    return {k: v for k, v in secrets.items() if k in allowed and v}


def _get_source(ctx: AppContext, sid: int) -> dict:
    s = ctx.db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "Source not found")
    return s


@router.get("/sources")
def list_sources(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return [_pub_source(s, ctx) for s in ctx.db.query("SELECT * FROM sources ORDER BY id")]


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    _check_interval(body.interval_seconds)
    if ctx.db.one("SELECT 1 FROM sources WHERE name=?", (body.name,)):
        raise HTTPException(409, "A source with this name exists")
    enc = ctx.secrets.encrypt(_clean_secrets(body.cloud, body.secrets))
    sid = ctx.db.execute(
        "INSERT INTO sources(name,cloud,config,secrets_enc,interval_seconds,enabled,created_at) VALUES(?,?,?,?,?,?,?)",
        (body.name, body.cloud, json.dumps(body.config), enc, body.interval_seconds, int(body.enabled), now_iso()))
    ctx.db.audit(admin["username"], "source.create", body.name, {"cloud": body.cloud})
    return _pub_source(_get_source(ctx, sid), ctx)


@router.patch("/sources/{sid}")
def patch_source(sid: int, body: SourcePatch, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    s = _get_source(ctx, sid)
    if body.interval_seconds is not None:
        _check_interval(body.interval_seconds)
        ctx.db.execute("UPDATE sources SET interval_seconds=? WHERE id=?", (body.interval_seconds, sid))
    if body.name:
        ctx.db.execute("UPDATE sources SET name=? WHERE id=?", (body.name, sid))
    if body.config is not None:
        ctx.db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(body.config), sid))
    if body.enabled is not None:
        ctx.db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(body.enabled), sid))
    if body.secrets:  # merge: blank values keep the stored secret
        cur = ctx.secrets.decrypt(s["secrets_enc"])
        cur.update(_clean_secrets(s["cloud"], body.secrets))
        ctx.db.execute("UPDATE sources SET secrets_enc=? WHERE id=?", (ctx.secrets.encrypt(cur), sid))
    ctx.db.audit(admin["username"], "source.update", s["name"])
    return _pub_source(_get_source(ctx, sid), ctx)


@router.delete("/sources/{sid}")
def delete_source(sid: int, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    s = _get_source(ctx, sid)
    ctx.db.execute("DELETE FROM sources WHERE id=?", (sid,))
    ctx.db.audit(admin["username"], "source.delete", s["name"])
    return {"ok": True}


@router.post("/sources/{sid}/test")
def test_source(sid: int, _: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    s = _get_source(ctx, sid)
    try:
        conn = ctx.connector_factory({**s, "config": json.loads(s["config"] or "{}")}, ctx.secrets.decrypt(s["secrets_enc"]))
        return {"ok": True, **conn.test()}
    except ConnectorError as e:
        return {"ok": False, "kind": e.kind, "error": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "unknown", "error": f"{type(e).__name__}: {e}"[:300]}


@router.post("/sources/{sid}/collect")
def collect_now(sid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    _get_source(ctx, sid)
    ctx.db.audit(user["username"], "source.collect", str(sid))
    return collect_source(ctx, sid)


class InjectIn(BaseModel):
    scenario: str


@router.get("/demo/scenarios")
def scenarios(_: dict = Depends(require("viewer"))):
    return SCENARIOS


@router.post("/sources/{sid}/demo/inject")
def inject(sid: int, body: InjectIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    s = _get_source(ctx, sid)
    if s["cloud"] != "demo":
        raise HTTPException(400, "Only demo sources support scenario injection")
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"scenario must be one of {list(SCENARIOS)}")
    cfg = json.loads(s["config"] or "{}")
    started = utcnow() - timedelta(minutes=12)  # so there is already a visible backlog
    cfg.update({"scenario": body.scenario, "scenario_started_at": to_iso(started)})
    ctx.db.execute("UPDATE sources SET config=?, last_collected_at=? WHERE id=?",
                   (json.dumps(cfg), to_iso(started - timedelta(minutes=2)), sid))
    ctx.db.audit(user["username"], "demo.inject", s["name"], {"scenario": body.scenario})
    return collect_source(ctx, sid)


# ---- logs ----
@router.get("/logs")
def search(q: str = "", severity: str = "", service: str = "", source_id: int = 0, minutes: int = 60,
           limit: int = 100, offset: int = 0, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    where, p = ["ts>=?"], [to_iso(utcnow() - timedelta(minutes=max(1, min(minutes, 10080))))]
    if severity:
        sevs = [x for x in severity.upper().split(",") if x in SEVERITIES]
        if sevs:
            where.append(f"severity IN ({','.join('?' * len(sevs))})")
            p += sevs
    if service:
        where.append("service=?")
        p.append(service)
    if source_id:
        where.append("source_id=?")
        p.append(source_id)
    for w in q.split()[:6]:
        where.append("message LIKE ? ESCAPE '\\'")
        p.append("%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    w = " AND ".join(where)
    total = ctx.db.one(f"SELECT COUNT(*) AS n FROM logs WHERE {w}", p)["n"]
    rows = ctx.db.query(f"SELECT id,source_id,ts,severity,service,host,message FROM logs WHERE {w} ORDER BY ts DESC LIMIT ? OFFSET ?",
                        p + [max(1, min(limit, 500)), max(0, offset)])
    return {"total": total, "logs": rows}


@router.get("/logs/services")
def log_services(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return [r["service"] for r in ctx.db.query("SELECT DISTINCT service FROM logs WHERE service IS NOT NULL ORDER BY service")]


# ---- incidents ----
@router.get("/incidents")
def list_incidents(status: str = "", severity: str = "", q: str = "", limit: int = 100,
                   _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    where, p = ["1=1"], []
    if status in ("open", "acknowledged", "resolved"):
        where.append("status=?")
        p.append(status)
    if severity.upper() in SEVERITIES:
        where.append("severity=?")
        p.append(severity.upper())
    if q:
        where.append("title LIKE ?")
        p.append(f"%{q}%")
    return ctx.db.query(
        f"SELECT * FROM incidents WHERE {' AND '.join(where)} ORDER BY CASE status WHEN 'resolved' THEN 1 ELSE 0 END, "
        f"last_seen DESC LIMIT ?", p + [max(1, min(limit, 500))])


@router.get("/incidents/{iid}")
def get_incident(iid: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    inc = ctx.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    if not inc:
        raise HTTPException(404, "Incident not found")
    invs = ctx.db.query("SELECT id,status,provider,model,created_by,created_at,finished_at,error FROM investigations "
                        "WHERE incident_id=? ORDER BY id DESC", (iid,))
    logs = ctx.db.query("SELECT id,ts,severity,service,host,message FROM logs WHERE fingerprint=? AND service IS ? "
                        "ORDER BY ts DESC LIMIT 30", (inc["fingerprint"], inc["service"]))
    return {**inc, "investigations": invs, "sample_logs": logs}


class IncidentPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|acknowledged|resolved)$")
    resolution_notes: str | None = Field(default=None, max_length=5000)


@router.patch("/incidents/{iid}")
def patch_incident(iid: int, body: IncidentPatch, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    if not ctx.db.one("SELECT 1 FROM incidents WHERE id=?", (iid,)):
        raise HTTPException(404, "Incident not found")
    if body.status:
        ctx.db.execute("UPDATE incidents SET status=?, resolved_at=? WHERE id=?",
                       (body.status, now_iso() if body.status == "resolved" else None, iid))
    if body.resolution_notes is not None:
        ctx.db.execute("UPDATE incidents SET resolution_notes=? WHERE id=?", (body.resolution_notes, iid))
    ctx.db.execute("UPDATE incidents SET updated_at=? WHERE id=?", (now_iso(), iid))
    ctx.db.audit(user["username"], "incident.update", str(iid), body.model_dump(exclude_none=True))
    return ctx.db.one("SELECT * FROM incidents WHERE id=?", (iid,))


@router.post("/incidents/{iid}/investigate", status_code=202)
def investigate(iid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    if not ctx.db.one("SELECT 1 FROM incidents WHERE id=?", (iid,)):
        raise HTTPException(404, "Incident not found")
    running = ctx.db.one("SELECT id FROM investigations WHERE incident_id=? AND status='running'", (iid,))
    if running:
        return {"investigation_id": running["id"], "status": "running"}
    inv = start_investigation(ctx, iid, user["username"])
    return {"investigation_id": inv, "status": "running"}


@router.get("/incidents/{iid}/report.html", response_class=HTMLResponse)
def report(iid: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    inc = ctx.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    if not inc:
        raise HTTPException(404, "Incident not found")
    inv = ctx.db.one("SELECT * FROM investigations WHERE incident_id=? AND status='done' ORDER BY id DESC LIMIT 1", (iid,))
    logs = ctx.db.query("SELECT id,ts,severity,service,message FROM logs WHERE fingerprint=? ORDER BY ts DESC LIMIT 15", (inc["fingerprint"],))
    return HTMLResponse(incident_report_html(inc, inv, logs))


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    r = ctx.db.one("SELECT * FROM investigations WHERE id=?", (inv_id,))
    if not r:
        raise HTTPException(404, "Investigation not found")
    r["result"] = json.loads(r["result"]) if r["result"] else None
    r["trace"] = json.loads(r["trace"]) if r["trace"] else []
    return r


# ---- overview ----
@router.get("/overview")
def overview(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    db = ctx.db
    now = utcnow()
    hour = to_iso(now - timedelta(minutes=60))
    start = now - timedelta(minutes=60)
    buckets = [0] * 13
    for r in db.query("SELECT ts FROM logs WHERE ts>=? AND severity IN ('HIGH','CRITICAL')", (hour,)):
        i = int((parse_ts(r["ts"]) - start).total_seconds() // 300)
        if 0 <= i < 13:
            buckets[i] += 1
    return {
        "logs_last_hour": db.one("SELECT COUNT(*) AS n FROM logs WHERE ts>=?", (hour,))["n"],
        "errors_last_hour": db.one("SELECT COUNT(*) AS n FROM logs WHERE ts>=? AND severity IN ('HIGH','CRITICAL')", (hour,))["n"],
        "open_incidents": db.query("SELECT severity, COUNT(*) AS n FROM incidents WHERE status!='resolved' GROUP BY severity"),
        "total_incidents": db.one("SELECT COUNT(*) AS n FROM incidents")["n"],
        "timeline": [{"start": to_iso(start + timedelta(minutes=5 * i)), "errors": c} for i, c in enumerate(buckets)],
        "top_services": db.query("SELECT service, COUNT(*) AS errors FROM logs WHERE ts>=? AND severity IN ('HIGH','CRITICAL') "
                                 "GROUP BY service ORDER BY errors DESC LIMIT 6", (hour,)),
        "sources": [{"id": s["id"], "name": s["name"], "cloud": s["cloud"], "last_status": s["last_status"],
                     "last_collected_at": s["last_collected_at"]} for s in db.query("SELECT * FROM sources ORDER BY id")],
        "llm": ctx.llm.safe_get().describe(),
    }
