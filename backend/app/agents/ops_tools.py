"""Read-only tools the operations agents can call. All of them only SELECT."""
from __future__ import annotations

from datetime import timedelta

from ..db import parse_ts, to_iso
from ..llm.base import ToolSpec
from ..normalize import SEVERITIES, rank
from .framework import AgentContext, Tool

MSG_MAX = 400


def _since(actx: AgentContext, minutes) -> str:
    try:
        m = max(1, min(int(minutes or 60), 1440))
    except (TypeError, ValueError):
        m = 60
    return to_iso(actx.anchor - timedelta(minutes=m))


def _until(actx: AgentContext) -> str:
    return to_iso(actx.anchor + timedelta(seconds=1))


def _sev_in(min_sev: str | None) -> list[str]:
    if not min_sev or min_sev.upper() not in SEVERITIES:
        return SEVERITIES[:]
    r = rank(min_sev.upper())
    return [s for s in SEVERITIES if rank(s) >= r]


def _row(r: dict) -> dict:
    return {"id": r["id"], "ts": r["ts"], "severity": r["severity"], "service": r["service"],
            "host": r["host"], "message": r["message"][:MSG_MAX]}


def list_sources(args, actx):
    rows = actx.app.db.query("SELECT id,name,cloud,last_collected_at,last_status FROM sources WHERE enabled=1")
    return {"sources": rows}


def search_logs(args, actx):
    db = actx.app.db
    sql = ["SELECT id,ts,severity,service,host,message FROM logs WHERE ts>=? AND ts<?"]
    p: list = [_since(actx, args.get("minutes")), _until(actx)]
    sevs = _sev_in(args.get("min_severity"))
    sql.append(f"AND severity IN ({','.join('?' * len(sevs))})")
    p += sevs
    if args.get("service"):
        sql.append("AND lower(service)=lower(?)")
        p.append(str(args["service"]))
    if args.get("source_id"):
        sql.append("AND source_id=?")
        p.append(int(args["source_id"]))
    for w in str(args.get("query") or "").split()[:6]:
        sql.append("AND message LIKE ? ESCAPE '\\'")
        p.append("%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    try:
        limit = max(1, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    sql.append("ORDER BY ts DESC LIMIT ?")
    p.append(limit)
    rows = db.query(" ".join(sql), p)
    for r in rows:
        actx.seen_log_ids.add(r["id"])
    return {"count": len(rows), "logs": [_row(r) for r in rows]}


def get_log_context(args, actx):
    db = actx.app.db
    base = db.one("SELECT * FROM logs WHERE id=?", (int(args.get("log_id", 0)),))
    if not base:
        return {"error": "log not found"}
    w = max(5, min(int(args.get("window_seconds") or 60), 600))
    t = parse_ts(base["ts"])
    rows = db.query(
        "SELECT id,ts,severity,service,host,message FROM logs WHERE source_id=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 40",
        (base["source_id"], to_iso(t - timedelta(seconds=w)), to_iso(t + timedelta(seconds=w))),
    )
    for r in rows:
        actx.seen_log_ids.add(r["id"])
    return {"center_log_id": base["id"], "logs": [_row(r) for r in rows]}


def top_error_patterns(args, actx):
    db = actx.app.db
    sevs = _sev_in(args.get("min_severity") or "MEDIUM")
    rows = db.query(
        f"""SELECT fingerprint, service, COUNT(*) AS cnt, MIN(ts) AS first_seen, MAX(ts) AS last_seen,
                   MAX(id) AS sample_id
            FROM logs WHERE ts>=? AND ts<? AND severity IN ({','.join('?' * len(sevs))})
            GROUP BY fingerprint, service ORDER BY cnt DESC LIMIT ?""",
        [_since(actx, args.get("minutes")), _until(actx), *sevs, max(1, min(int(args.get("limit") or 8), 20))],
    )
    out = []
    for r in rows:
        s = db.one("SELECT id,message,severity FROM logs WHERE id=?", (r["sample_id"],))
        actx.seen_log_ids.add(s["id"])
        out.append({"fingerprint": r["fingerprint"], "service": r["service"], "count": r["cnt"],
                    "severity": s["severity"], "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                    "sample": s["message"][:MSG_MAX], "sample_log_id": s["id"]})
    return {"patterns": out}


def error_timeline(args, actx):
    minutes = max(5, min(int(args.get("minutes") or 60), 1440))
    bucket = max(1, min(int(args.get("bucket_minutes") or 5), 60))
    sql = "SELECT ts, service FROM logs WHERE ts>=? AND ts<? AND severity IN ('HIGH','CRITICAL')"
    p: list = [_since(actx, minutes), _until(actx)]
    if args.get("service"):
        sql += " AND lower(service)=lower(?)"
        p.append(str(args["service"]))
    start = actx.anchor - timedelta(minutes=minutes)
    counts: dict[int, int] = {}
    for r in actx.app.db.query(sql, p):
        idx = int((parse_ts(r["ts"]) - start).total_seconds() // (bucket * 60))
        counts[idx] = counts.get(idx, 0) + 1
    n = minutes // bucket + 1
    return {"bucket_minutes": bucket,
            "buckets": [{"start": to_iso(start + timedelta(minutes=i * bucket)), "errors": counts.get(i, 0)} for i in range(n)]}


def get_incident(args, actx):
    r = actx.app.db.one("SELECT * FROM incidents WHERE id=?", (int(args.get("incident_id", 0)),))
    return r or {"error": "incident not found"}


def find_similar_incidents(args, actx):
    db = actx.app.db
    fp = None
    if args.get("incident_id"):
        inc = db.one("SELECT fingerprint FROM incidents WHERE id=?", (int(args["incident_id"]),))
        fp = inc["fingerprint"] if inc else None
    rows = []
    if fp:
        rows = db.query("SELECT id,title,service,status,resolution_notes,updated_at FROM incidents "
                        "WHERE fingerprint=? AND id!=? ORDER BY updated_at DESC LIMIT 5", (fp, int(args["incident_id"])))
    q = str(args.get("query") or "").strip()
    if q and len(rows) < 5:
        for w in q.split()[:3]:
            rows += db.query("SELECT id,title,service,status,resolution_notes,updated_at FROM incidents "
                             "WHERE title LIKE ? ORDER BY updated_at DESC LIMIT 3", (f"%{w}%",))
    seen, out = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return {"incidents": out[:5]}


def search_runbooks(args, actx):
    kb = actx.app.kb
    hits = kb.search(str(args.get("query") or ""), k=3) if kb else []
    return {"results": hits}


def _spec(name, desc, props, required=None):
    return ToolSpec(name, desc, {"type": "object", "properties": props, "required": required or []})


_MIN = {"type": "integer", "description": "Look back this many minutes (default 60, max 1440)"}
_SEVP = {"type": "string", "enum": SEVERITIES, "description": "Minimum severity"}

OPS_TOOLS: list[Tool] = [
    Tool(_spec("list_sources", "List connected log sources.", {}), list_sources),
    Tool(_spec("search_logs", "Search collected logs. All words in 'query' must appear. Returns newest first with log ids.",
               {"query": {"type": "string"}, "service": {"type": "string"}, "source_id": {"type": "integer"},
                "min_severity": _SEVP, "minutes": _MIN, "limit": {"type": "integer", "description": "max 50"}}), search_logs),
    Tool(_spec("get_log_context", "Logs from the same source shortly before/after one log id.",
               {"log_id": {"type": "integer"}, "window_seconds": {"type": "integer"}}, ["log_id"]), get_log_context),
    Tool(_spec("top_error_patterns", "Group recent errors by pattern; shows count, first/last seen and a sample log id.",
               {"minutes": _MIN, "min_severity": _SEVP, "limit": {"type": "integer"}}), top_error_patterns),
    Tool(_spec("error_timeline", "Count of HIGH/CRITICAL logs per time bucket, to see when a problem started.",
               {"minutes": _MIN, "bucket_minutes": {"type": "integer"}, "service": {"type": "string"}}), error_timeline),
    Tool(_spec("get_incident", "Get one incident by id.", {"incident_id": {"type": "integer"}}, ["incident_id"]), get_incident),
    Tool(_spec("find_similar_incidents", "Past incidents with the same pattern or similar title, with resolution notes.",
               {"incident_id": {"type": "integer"}, "query": {"type": "string"}}), find_similar_incidents),
    Tool(_spec("search_runbooks", "Search runbooks and documentation (keyword/BM25 ranking).",
               {"query": {"type": "string"}}, ["query"]), search_runbooks),
]
