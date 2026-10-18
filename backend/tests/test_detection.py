from datetime import timedelta
from app.db import now_iso, to_iso, utcnow
from app.services.detection import detect_incidents
from app.services.scheduler import Scheduler
from app.services.collector import collect_source, purge_old_logs
from tests.conftest import inject


import itertools
_ctr = itertools.count()


def add_logs(env, n, sev="HIGH", msg="[svc] db timeout after {i}ms", svc="svc"):
    sid = env.ctx.db.one("SELECT id FROM sources")["id"]
    from app.normalize import fingerprint
    with env.ctx.db.connect() as c:
        for i in range(n):
            m = msg.format(i=i)
            c.execute("INSERT INTO logs(source_id,uid,ts,severity,service,message,fingerprint) VALUES(?,?,?,?,?,?,?)",
                      (sid, f"u{next(_ctr)}", to_iso(utcnow()), sev, svc, m, fingerprint(svc, m)))


def test_threshold_per_severity(env):
    add_logs(env, 1, "HIGH")
    assert detect_incidents(env.ctx) == []          # HIGH needs 2
    add_logs(env, 1, "CRITICAL", msg="[svc] panic")
    assert len(detect_incidents(env.ctx)) == 1      # CRITICAL needs 1
    add_logs(env, 4, "MEDIUM", msg="[svc] warn slow {i}")
    assert detect_incidents(env.ctx) == []          # MEDIUM needs 5
    add_logs(env, 1, "MEDIUM", msg="[svc] warn slow {i}x")


def test_same_pattern_groups_into_one_incident_and_refreshes(env):
    add_logs(env, 3)
    first = detect_incidents(env.ctx)
    assert len(first) == 1
    add_logs(env, 2, msg="[svc] db timeout after {i}ms again")  # different pattern -> separate
    add_logs(env, 3, msg="[svc] db timeout after {i}ms")
    detect_incidents(env.ctx)
    rows = env.ctx.db.query("SELECT * FROM incidents ORDER BY id")
    assert len(rows) == 2 and rows[0]["event_count"] >= 3


def test_resolved_incident_reopens_quickly_else_new(env):
    add_logs(env, 3)
    iid = detect_incidents(env.ctx)[0]
    env.client.patch(f"/api/incidents/{iid}", json={"status": "resolved"})
    add_logs(env, 1, msg="[svc] db timeout after {i}ms", sev="HIGH")
    assert detect_incidents(env.ctx) == [iid]
    assert env.ctx.db.one("SELECT status FROM incidents WHERE id=?", (iid,))["status"] == "open"


def test_collect_is_idempotent_no_duplicates(env):
    sid, first = inject(env)
    n = env.ctx.db.one("SELECT COUNT(*) AS n FROM logs")["n"]
    collect_source(env.ctx, sid)
    again = env.ctx.db.one("SELECT COUNT(*) AS n FROM logs")["n"]
    assert again >= n and env.ctx.db.one("SELECT COUNT(*) AS n FROM incidents WHERE status='open'")["n"] == len(first["incidents"])
    collect_source(env.ctx, sid); collect_source(env.ctx, sid)
    uids = env.ctx.db.query("SELECT source_id,uid,COUNT(*) AS c FROM logs GROUP BY source_id,uid HAVING c>1")
    assert uids == []


def test_logs_are_redacted_before_storage(env):
    sid = env.ctx.db.one("SELECT id FROM sources")["id"]
    from app.connectors.base import LogRecord
    class C:
        def fetch(self, s, u, limit=0):
            return [LogRecord(uid="1", ts=utcnow() - timedelta(seconds=5), message="login failed password=hunter2 user a@b.com")]
    env.ctx.connector_factory = lambda src, sec: C()
    collect_source(env.ctx, sid)
    msg = env.ctx.db.one("SELECT message FROM logs WHERE uid='1'")["message"]
    assert "hunter2" not in msg and "a@b.com" not in msg


def test_scheduler_runs_due_sources_only(env):
    sid = env.ctx.db.one("SELECT id FROM sources")["id"]
    env.ctx.db.execute("UPDATE sources SET interval_seconds=30, last_collected_at=? WHERE id=?", (to_iso(utcnow() - timedelta(seconds=120)), sid))
    s = Scheduler(env.ctx)
    assert s.tick() == [sid]
    import time; time.sleep(0.5)
    assert s.tick() == []  # just collected, not due
    env.ctx.db.execute("UPDATE sources SET interval_seconds=0 WHERE id=?", (sid,))
    env.ctx.db.execute("UPDATE sources SET last_collected_at=NULL WHERE id=?", (sid,))
    assert Scheduler(env.ctx).tick() == []  # manual-only


def test_retention_purge(env):
    add_logs(env, 2)
    env.ctx.db.execute("UPDATE logs SET ts=?", (to_iso(utcnow() - timedelta(days=30)),))
    assert purge_old_logs(env.ctx) == 2


def test_auto_pipeline_respects_hourly_cap(env):
    env.client.put("/api/settings/detection", json={"auto_pipeline": True, "auto_per_hour": 0})
    inject(env)
    import time; time.sleep(0.3)
    assert env.ctx.db.one("SELECT COUNT(*) AS n FROM pipeline_runs")["n"] == 0
