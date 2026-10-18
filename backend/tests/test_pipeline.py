import json
import time

import httpx

from app.pipeline import run_pipeline
from app.pipeline.classify import classify, detect_category, priority_for
from app.pipeline.rca_reco import estimate_eta
from app.services import activity, guide
from app.services import integrations as integ
from conftest import inject


def first_incident(env):
    inject(env)
    inc = env.ctx.db.one("SELECT * FROM incidents ORDER BY id LIMIT 1")
    assert inc
    return inc


def acts(env, run):
    return {r["agent"]: r for r in env.ctx.db.query("SELECT * FROM agent_activity WHERE run_id=?", (run,))}


def test_full_pipeline_runs_all_nine_stages(env):
    inc = first_incident(env)
    run = run_pipeline(env.ctx, inc["id"], "admin", "manual")
    a = acts(env, run)
    assert set(a) == {"log_monitor", "data_sanitizer", "dedup_engine", "classifier", "rca_correlator", "recommender", "notifier", "documenter", "servicenow"}
    assert all(r["status"] in ("ok", "skipped") for r in a.values()), {k: (v["status"], v["error"]) for k, v in a.items()}
    assert a["servicenow"]["status"] == "skipped"          # not configured, honestly reported
    row = env.ctx.db.one("SELECT priority,score,category,eta_min,eta_max FROM incidents WHERE id=?", (inc["id"],))
    assert row["priority"] in ("P1", "P2", "P3", "P4") and 0 <= row["score"] <= 100 and row["category"]
    assert row["eta_min"] and row["eta_max"] >= row["eta_min"]
    assert env.ctx.db.one("SELECT status FROM pipeline_runs WHERE id=?", (run,))["status"] == "done"


def test_pipeline_api_and_downloads(env):
    inc = first_incident(env)
    r = env.client.post(f"/api/incidents/{inc['id']}/pipeline")
    assert r.status_code == 202
    for _ in range(100):
        v = env.client.get(f"/api/incidents/{inc['id']}/pipeline").json()
        if v["run"] and v["run"]["status"] != "running":
            break
        time.sleep(0.1)
    assert v["run"]["status"] == "done"
    assert [s["agent"] for s in v["run"]["stages"]][:2] == ["log_monitor", "data_sanitizer"]
    assert any(n["channel"] == "dashboard" for n in v["notifications"])
    pdf = env.client.get(f"/api/incidents/{inc['id']}/resolution.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    txt = env.client.get(f"/api/incidents/{inc['id']}/resolution.txt")
    assert "FIX IT NOW" in txt.text.upper() and "Estimated time to resolve" in txt.text
    assert env.client.get(f"/api/incidents/{inc['id']}/resolution.docx").status_code == 404


def test_resolution_guide_404_before_pipeline(env):
    inc = first_incident(env)
    assert env.client.get(f"/api/incidents/{inc['id']}/resolution.pdf").status_code == 404


def test_agent_status_lists_all_agents(env):
    d = env.client.get("/api/agents").json()
    ids = {a["id"] for a in d["agents"]}
    assert {"log_monitor", "rca_correlator", "recommender", "chat_agent", "code_assistant", "pr_reviewer", "scheduler"} <= ids
    sn = next(a for a in d["agents"] if a["id"] == "servicenow")
    assert sn["status"] == "not_configured"
    sch = next(a for a in d["agents"] if a["id"] == "scheduler")
    assert sch["status"] == "disabled"          # tests disable it


def test_running_status_and_restart_cleanup(env):
    aid = activity.start(env.ctx, "log_monitor")
    d = env.client.get("/api/agents").json()
    assert next(a for a in d["agents"] if a["id"] == "log_monitor")["status"] == "running"
    assert activity.mark_interrupted(env.ctx) == 1
    d = env.client.get("/api/agents").json()
    assert next(a for a in d["agents"] if a["id"] == "log_monitor")["status"] == "error"


def test_classifier_rules():
    assert detect_category(["HikariPool-1 - Connection is not available, pool exhausted"]) == "resource_exhaustion"
    assert detect_category(["upstream request timeout 504"]) == "dependency_failure"
    assert detect_category(["hello"]) == "unknown"
    assert priority_for(85) == "P1" and priority_for(60) == "P2" and priority_for(40) == "P3" and priority_for(10) == "P4"


def test_score_is_explained(env):
    inc = first_incident(env)
    r = classify(env.ctx, inc)
    assert sum(f["points"] for f in r["factors"]) >= r["score"] or r["score"] == 100
    assert {f["name"] for f in r["factors"]} >= {"severity", "volume", "blast radius", "trend"}


def test_duplicate_incident_skips_alerting(env):
    inc = first_incident(env)
    now = env.ctx.db.one("SELECT first_seen FROM incidents WHERE id=?", (inc["id"],))["first_seen"]
    dup = env.ctx.db.execute(
        "INSERT INTO incidents(title,status,severity,fingerprint,service,source_id,first_seen,last_seen,event_count,sample_log_id,created_at,updated_at) "
        "SELECT title,'open',severity,fingerprint,service,source_id,first_seen,last_seen,event_count,sample_log_id,created_at,updated_at FROM incidents WHERE id=?", (inc["id"],))
    run = run_pipeline(env.ctx, dup, "admin")
    a = acts(env, run)
    assert env.ctx.db.one("SELECT duplicate_of FROM incidents WHERE id=?", (dup,))["duplicate_of"] == inc["id"]
    assert a["notifier"]["status"] == "skipped" and a["rca_correlator"]["status"] == "skipped"


def test_eta_learns_from_history(env):
    inc = first_incident(env)
    cat = "resource_exhaustion"
    assert "baseline" in estimate_eta(env.ctx, {**inc, "category": cat})["basis"]
    for mins in (10, 20, 30, 40):
        env.ctx.db.execute(
            "INSERT INTO incidents(title,status,severity,fingerprint,service,first_seen,last_seen,event_count,created_at,updated_at,category,resolved_at) "
            "VALUES('old','resolved','HIGH',?,?,?,?,1,'x','x',?,?)",
            (f"f{mins}", "payments", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", cat, f"2026-01-01T00:{mins:02d}:00Z"))
    e = estimate_eta(env.ctx, {**inc, "category": cat})
    assert e["samples"] == 4 and "similar resolved" in e["basis"] and 10 <= e["min"] <= e["max"] <= 40


def test_resolving_sets_resolved_at(env):
    inc = first_incident(env)
    env.client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})
    assert env.ctx.db.one("SELECT resolved_at FROM incidents WHERE id=?", (inc["id"],))["resolved_at"]
    env.client.patch(f"/api/incidents/{inc['id']}", json={"status": "open"})
    assert env.ctx.db.one("SELECT resolved_at FROM incidents WHERE id=?", (inc["id"],))["resolved_at"] is None


# ---------- notifier / integrations / servicenow ----------
def test_integration_secrets_never_returned(env):
    r = env.client.put("/api/integrations/pagerduty", json={"enabled": True, "routing_key": "R0UT1NGKEYSECRET", "min_priority": "P2"})
    assert r.status_code == 200 and r.json()["has_routing_key"] and "R0UT1NGKEYSECRET" not in r.text
    assert "R0UT1NGKEYSECRET" not in env.client.get("/api/integrations").text
    raw = json.dumps(env.ctx.db.get_setting("integrations"))
    assert "R0UT1NGKEYSECRET" not in raw       # encrypted at rest
    env.client.put("/api/integrations/pagerduty", json={"min_priority": "P3"})
    assert integ.get_secrets(env.ctx)["pagerduty"]["routing_key"] == "R0UT1NGKEYSECRET"   # blank keeps secret


def test_integration_validation(env):
    assert env.client.put("/api/integrations/slack", json={"url": "http://x.com/h"}).status_code == 400
    assert env.client.put("/api/integrations/servicenow", json={"instance_url": "http://dev.service-now.com"}).status_code == 400
    assert env.client.put("/api/integrations/nope", json={}).status_code == 400
    assert env.client.put("/api/integrations/slack", json={"enabled": True}, headers={"Authorization": ""}).status_code == 401


def test_notifier_routes_by_priority_and_isolates_failures(env, monkeypatch):
    from app.pipeline.common import State
    from app.pipeline.notify_doc import stage_notifier
    inc = first_incident(env)
    env.ctx.db.execute("UPDATE incidents SET priority='P2' WHERE id=?", (inc["id"],))
    integ.update_integration(env.ctx, "slack", {"enabled": True, "url": "https://hooks.example.com/x", "min_priority": "P3"})
    integ.update_integration(env.ctx, "pagerduty", {"enabled": True, "routing_key": "K", "min_priority": "P1"})   # P2 below threshold
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"ok": True})
    st = State(run_id=1, incident_id=inc["id"], user=None, incident=env.ctx.db.one("SELECT * FROM incidents WHERE id=?", (inc["id"],)))
    out = stage_notifier(env.ctx, st, {"slack": httpx.MockTransport(handler), "pagerduty": httpx.MockTransport(handler)})
    assert out.result["channels"]["slack"]["status"] == "sent"
    assert out.result["channels"]["pagerduty"]["status"] == "skipped"
    assert calls == ["https://hooks.example.com/x"]
    # second run inside 30 min does not re-send
    out2 = stage_notifier(env.ctx, st, {"slack": httpx.MockTransport(handler)})
    assert out2.result["channels"]["slack"]["status"] == "skipped" and len(calls) == 1


def test_notifier_fails_when_every_external_channel_fails(env):
    import pytest
    from app.pipeline.common import State
    from app.pipeline.notify_doc import stage_notifier
    inc = first_incident(env)
    integ.update_integration(env.ctx, "slack", {"enabled": True, "url": "https://hooks.example.com/x", "min_priority": "P4"})
    env.ctx.db.execute("UPDATE incidents SET priority='P1' WHERE id=?", (inc["id"],))
    st = State(run_id=1, incident_id=inc["id"], user=None, incident=env.ctx.db.one("SELECT * FROM incidents WHERE id=?", (inc["id"],)))
    with pytest.raises(RuntimeError):
        stage_notifier(env.ctx, st, {"slack": httpx.MockTransport(lambda r: httpx.Response(500))})


def test_pagerduty_payload():
    from app.services.notify import send_pagerduty
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(202, json={"status": "success"})
    inc = {"id": 3, "title": "x", "severity": "HIGH", "event_count": 4, "priority": "P1", "fingerprint": "abc", "service": "pay"}
    send_pagerduty("KEY", inc, "https://l/1", httpx.MockTransport(h))
    assert seen["routing_key"] == "KEY" and seen["event_action"] == "trigger" and seen["payload"]["severity"] == "critical"
    assert seen["dedup_key"] == "logfix-abc-pay"


def test_servicenow_ticket_created_once(env):
    inc = first_incident(env)
    env.ctx.db.execute("UPDATE incidents SET priority='P1' WHERE id=?", (inc["id"],))
    integ.update_integration(env.ctx, "servicenow", {"enabled": True, "instance_url": "https://dev1.service-now.com", "username": "u", "password": "p", "min_priority": "P2"})
    from app.pipeline.common import State
    from app.pipeline.notify_doc import stage_servicenow
    sent = []

    def h(req):
        sent.append(json.loads(req.content))
        return httpx.Response(201, json={"result": {"number": "INC0010001", "sys_id": "abc123"}})
    st = State(run_id=1, incident_id=inc["id"], user=None, incident=env.ctx.db.one("SELECT * FROM incidents WHERE id=?", (inc["id"],)))
    out = stage_servicenow(env.ctx, st, httpx.MockTransport(h))
    assert out.result["number"] == "INC0010001" and "Impact 1, Urgency 1" in out.summary
    assert sent[0]["impact"] == 1 and sent[0]["urgency"] == 1
    st.incident = env.ctx.db.one("SELECT * FROM incidents WHERE id=?", (inc["id"],))
    again = stage_servicenow(env.ctx, st, httpx.MockTransport(h))
    assert again.status == "skipped" and len(sent) == 1


def test_servicenow_error_is_reported(env):
    import pytest
    from app.services.servicenow import create_incident
    with pytest.raises(RuntimeError, match="401"):
        create_incident({"instance_url": "https://x.service-now.com", "username": "u", "password": "p"},
                        {"id": 1, "title": "t", "priority": "P1"}, "s", "d", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))


def test_guide_renderers():
    md = "# Title\n\n**bold** text <script>\n\n## Steps\n1. one\n- bullet & more\n\n---\nend"
    assert "TITLE" in guide.to_text(md) and "**" not in guide.to_text(md)
    assert guide.to_pdf(md)[:4] == b"%PDF"


def test_secrets_in_logs_do_not_reach_guide(env):
    inc = first_incident(env)
    sid = env.ctx.db.one("SELECT id FROM sources LIMIT 1")["id"]
    from app.redact import redact
    env.ctx.db.execute("INSERT INTO logs(source_id,uid,ts,severity,service,message,fingerprint) VALUES(?,?,?,?,?,?,?)",
                       (sid, "leak1", inc["last_seen"], "HIGH", inc["service"], "boom password=hunter2hunter2", inc["fingerprint"]))
    run = run_pipeline(env.ctx, inc["id"], "admin")
    md = json.loads(acts(env, run)["documenter"]["result"])["markdown"]
    assert "hunter2" not in md
