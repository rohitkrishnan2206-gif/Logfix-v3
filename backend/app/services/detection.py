"""Incident detection: group recent logs by fingerprint and open/refresh incidents."""
from __future__ import annotations

from datetime import timedelta

from ..context import AppContext
from ..db import now_iso, to_iso, utcnow, parse_ts
from ..normalize import SEVERITIES, rank

DEFAULTS = {
    "lookback_minutes": 60,
    "thresholds": {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 5},
    "auto_investigate": False,
    "auto_pipeline": True,
    "auto_per_hour": 5,
    "webhook_url": "",
}
MAX_NEW_PER_CALL = 25
REOPEN_MINUTES = 30


def get_detection_settings(db) -> dict:
    s = dict(DEFAULTS)
    stored = db.get_setting("detection", {}) or {}
    s.update({k: v for k, v in stored.items() if k in DEFAULTS})
    s["thresholds"] = {**DEFAULTS["thresholds"], **(stored.get("thresholds") or {})}
    return s


def detect_incidents(actx: AppContext, source_id: int | None = None) -> list[int]:
    """Returns ids of NEWLY created or reopened incidents (not mere refreshes)."""
    db = actx.db
    cfg = get_detection_settings(db)
    since = to_iso(utcnow() - timedelta(minutes=int(cfg["lookback_minutes"])))
    sql = ("SELECT fingerprint, service, source_id, COUNT(*) AS cnt, MIN(ts) AS first_seen, MAX(ts) AS last_seen, "
           "MAX(CASE severity WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 1 ELSE 0 END) AS sev_rank, "
           "MAX(id) AS sample_id FROM logs WHERE ts>=? AND severity IN ('MEDIUM','HIGH','CRITICAL')")
    params: list = [since]
    if source_id:
        sql += " AND source_id=?"
        params.append(source_id)
    sql += " GROUP BY fingerprint, service, source_id"
    changed: list[int] = []
    now = now_iso()
    for g in db.query(sql, params):
        sev = SEVERITIES[g["sev_rank"]]
        threshold = int(cfg["thresholds"].get(sev, 5))
        if g["cnt"] < threshold:
            continue
        sample = db.one("SELECT id,message FROM logs WHERE id=?", (g["sample_id"],))
        existing = db.one(
            "SELECT * FROM incidents WHERE fingerprint=? AND service IS ? AND status IN ('open','acknowledged')",
            (g["fingerprint"], g["service"]),
        )
        if existing:
            new_sev = sev if rank(sev) > rank(existing["severity"]) else existing["severity"]
            db.execute(
                "UPDATE incidents SET last_seen=?, event_count=?, severity=?, updated_at=? WHERE id=?",
                (g["last_seen"], max(g["cnt"], existing["event_count"]), new_sev, now, existing["id"]),
            )
            continue
        cutoff = to_iso(utcnow() - timedelta(minutes=REOPEN_MINUTES))
        recent = db.one(
            "SELECT * FROM incidents WHERE fingerprint=? AND service IS ? AND status='resolved' AND updated_at>=? "
            "ORDER BY updated_at DESC LIMIT 1", (g["fingerprint"], g["service"], cutoff))
        if recent:
            db.execute("UPDATE incidents SET status='open', last_seen=?, event_count=?, updated_at=? WHERE id=?",
                       (g["last_seen"], g["cnt"], now, recent["id"]))
            db.audit("system", "incident.reopen", str(recent["id"]))
            changed.append(recent["id"])
            continue
        if len(changed) >= MAX_NEW_PER_CALL:
            continue
        title = f"{g['service'] or 'unknown'}: {sample['message'][:90]}"
        iid = db.execute(
            "INSERT INTO incidents(title,status,severity,fingerprint,service,source_id,first_seen,last_seen,event_count,"
            "sample_log_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, "open", sev, g["fingerprint"], g["service"], g["source_id"], g["first_seen"], g["last_seen"],
             g["cnt"], sample["id"], now, now),
        )
        changed.append(iid)
    return changed
