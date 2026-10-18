"""Pull logs from a source, normalise, redact, store, then run detection."""
from __future__ import annotations

import json
from datetime import timedelta

from ..connectors.base import ConnectorError
from ..context import AppContext
from ..db import now_iso, parse_ts, to_iso, utcnow
from ..normalize import extract_service, fingerprint, parse_severity
from ..redact import redact, redact_counted
from . import activity
from .detection import detect_incidents, get_detection_settings

OVERLAP_SECONDS = 60
DEFAULT_BACKFILL_MINUTES = 30


def collect_source(actx: AppContext, source_id: int) -> dict:
    src = actx.db.one("SELECT name FROM sources WHERE id=?", (source_id,))
    with activity.Tracked(actx, "collector") as t:
        out = _collect(actx, source_id)
        name = src["name"] if src else str(source_id)
        if out.get("error"):
            t.fail(out["error"], summary=f"{name}: collection failed")
        else:
            t.ok(f"{name}: fetched {out['fetched']}, stored {out['inserted']}, {len(out['incidents'])} new incident(s)")
    return out


def _collect(actx: AppContext, source_id: int) -> dict:
    db = actx.db
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        return {"error": "source not found"}
    until = utcnow()
    last = parse_ts(src["last_collected_at"])
    since = (last - timedelta(seconds=OVERLAP_SECONDS)) if last else until - timedelta(minutes=DEFAULT_BACKFILL_MINUTES)
    try:
        secrets = actx.secrets.decrypt(src["secrets_enc"])
        conn = actx.connector_factory({**src, "config": json.loads(src["config"] or "{}")}, secrets)
        records = conn.fetch(since, until)
    except ConnectorError as e:
        db.execute("UPDATE sources SET last_status='error', last_error=? WHERE id=?", (f"{e.kind}: {e.message}"[:500], source_id))
        return {"error": f"{e.kind}: {e.message}", "fetched": 0, "inserted": 0, "incidents": []}
    except Exception as e:  # noqa: BLE001
        db.execute("UPDATE sources SET last_status='error', last_error=? WHERE id=?", (f"{type(e).__name__}: {e}"[:500], source_id))
        return {"error": f"{type(e).__name__}: {e}", "fetched": 0, "inserted": 0, "incidents": []}

    inserted = 0
    with db.connect() as c:
        for r in records:
            msg, nred = redact_counted(r.message[:4000])
            # A "[service]" tag in the message wins; else the connector's hint; else "unknown".
            service = extract_service(msg, r.service or "unknown")
            sev = parse_severity(msg, r.level)
            cur = c.execute(
                "INSERT OR IGNORE INTO logs(source_id,uid,ts,severity,service,host,message,fingerprint,raw,redactions) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source_id, r.uid, to_iso(r.ts), sev, service, r.host, msg, fingerprint(service, msg),
                 redact(json.dumps(r.raw, default=str))[:2000], nred),
            )
            inserted += cur.rowcount
    db.execute("UPDATE sources SET last_collected_at=?, last_status='ok', last_error=NULL WHERE id=?", (to_iso(until), source_id))

    new_ids = detect_incidents(actx, source_id)
    cfg = get_detection_settings(db)
    for iid in new_ids:
        inc = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
        if cfg.get("auto_pipeline", True):
            hour_ago = to_iso(utcnow() - timedelta(hours=1))
            used = db.one("SELECT COUNT(*) AS n FROM pipeline_runs WHERE trigger='auto' AND created_at>=?", (hour_ago,))["n"]
            if used < int(cfg.get("auto_per_hour", 5)):
                from ..pipeline import start_pipeline
                start_pipeline(actx, iid, user="system", trigger="auto")
            else:
                db.audit("system", "pipeline.rate_limited", str(iid), "hourly auto-pipeline cap reached")
    return {"fetched": len(records), "inserted": inserted, "incidents": new_ids, "error": None}


def purge_old_logs(actx: AppContext) -> int:
    cutoff = to_iso(utcnow() - timedelta(days=actx.settings.log_retention_days))
    with actx.db.connect() as c:
        c.execute("DELETE FROM agent_activity WHERE started_at<?", (cutoff,))
        return c.execute("DELETE FROM logs WHERE ts<?", (cutoff,)).rowcount
