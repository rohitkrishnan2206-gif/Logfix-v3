"""Agent activity log + live status. One row per agent execution (pipeline stage, chat answer,
code question, PR review, collection run). The Agents page reads from here."""
from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from ..context import AppContext
from ..db import now_iso, parse_ts, to_iso, utcnow

STALE_RUNNING_MINUTES = 15

# id, display name, kind, uses an LLM, what it does
AGENTS: list[dict[str, Any]] = [
    {"id": "collector", "name": "Log Collector", "kind": "service", "llm": False,
     "desc": "Pulls logs from AWS, Azure, GCP and demo sources on a schedule, redacts and stores them"},
    {"id": "log_monitor", "name": "Log Monitor", "kind": "pipeline", "llm": True,
     "desc": "Measures the error spike and names the anomaly"},
    {"id": "data_sanitizer", "name": "Data Sanitizer", "kind": "pipeline", "llm": False,
     "desc": "Checks the evidence for PII/secrets and creates a SHA-256 fingerprint"},
    {"id": "dedup_engine", "name": "Dedup Engine", "kind": "pipeline", "llm": False,
     "desc": "Finds duplicates across sources in a 30-minute window and related incidents"},
    {"id": "classifier", "name": "Classifier", "kind": "pipeline", "llm": False,
     "desc": "Scores the incident 0-100, assigns P1-P4 and a category (transparent rules)"},
    {"id": "rca_correlator", "name": "RCA Correlator", "kind": "pipeline", "llm": True,
     "desc": "Tool-using agent: finds the root cause with cited log evidence"},
    {"id": "recommender", "name": "Recommender", "kind": "pipeline", "llm": True,
     "desc": "Remediation steps, rollback, verification and a resolution-time estimate"},
    {"id": "notifier", "name": "Notifier", "kind": "pipeline", "llm": False,
     "desc": "Routes alerts to Slack/Teams, PagerDuty, email and the dashboard by priority"},
    {"id": "documenter", "name": "Documenter", "kind": "pipeline", "llm": True,
     "desc": "Writes the incident report, stakeholder update and downloadable resolution guide"},
    {"id": "servicenow", "name": "ServiceNow", "kind": "pipeline", "llm": False,
     "desc": "Creates an ITSM ticket with impact/urgency from the classification"},
    {"id": "chat_agent", "name": "Chat Assistant", "kind": "interactive", "llm": True,
     "desc": "Answers questions about your logs, incidents and runbooks"},
    {"id": "code_assistant", "name": "Code Assistant", "kind": "interactive", "llm": True,
     "desc": "Reads linked repositories and proposes fixes for human approval"},
    {"id": "pr_reviewer", "name": "PR Reviewer", "kind": "interactive", "llm": True,
     "desc": "Reviews GitHub pull requests when the webhook fires"},
    {"id": "scheduler", "name": "Scheduler", "kind": "service", "llm": False,
     "desc": "Background loop that triggers due collections and log retention"},
]
AGENT_IDS = {a["id"] for a in AGENTS}


def start(actx: AppContext, agent: str, run_id: int | None = None, incident_id: int | None = None,
          user: str | None = None, provider: str | None = None, model: str | None = None) -> int:
    return actx.db.execute(
        "INSERT INTO agent_activity(agent,run_id,incident_id,status,started_at,provider,model,created_by) VALUES(?,?,?,?,?,?,?,?)",
        (agent, run_id, incident_id, "running", now_iso(), provider, model, user))


def finish(actx: AppContext, act_id: int, status: str, summary: str = "", result: Any = None, error: str | None = None) -> None:
    row = actx.db.one("SELECT started_at FROM agent_activity WHERE id=?", (act_id,))
    ms = int((utcnow() - parse_ts(row["started_at"])).total_seconds() * 1000) if row else None
    actx.db.execute(
        "UPDATE agent_activity SET status=?, finished_at=?, ms=?, summary=?, result=?, error=? WHERE id=?",
        (status, now_iso(), ms, (summary or "")[:500], json.dumps(result, default=str) if result is not None else None,
         (error or None) and error[:1000], act_id))


class Tracked:
    """with Tracked(actx, 'chat_agent') as t: ...; t.ok('summary') / t.fail(err). Unhandled exceptions mark failed."""

    def __init__(self, actx, agent, **kw):
        self.actx, self.agent, self.kw, self.id, self._done = actx, agent, kw, 0, False

    def __enter__(self):
        self.id = start(self.actx, self.agent, **self.kw)
        return self

    def ok(self, summary="", result=None, status="ok"):
        self._done = True
        finish(self.actx, self.id, status, summary, result)

    def fail(self, error, summary=""):
        self._done = True
        finish(self.actx, self.id, "failed", summary, None, str(error))

    def __exit__(self, et, ev, tb):
        if not self._done:
            finish(self.actx, self.id, "failed" if et else "ok", "", None, f"{et.__name__}: {ev}" if et else None)
        return False


def mark_interrupted(actx: AppContext) -> int:
    """On startup nothing can really be running: close rows left 'running' by a crash/restart."""
    n = 0
    for r in actx.db.query("SELECT id FROM agent_activity WHERE status='running'"):
        finish(actx, r["id"], "failed", "", None, "interrupted by restart")
        n += 1
    actx.db.execute("UPDATE pipeline_runs SET status='failed', finished_at=? WHERE status='running'", (now_iso(),))
    return n


def _configured(actx: AppContext, agent: str) -> tuple[bool, str]:
    if agent == "servicenow":
        from .integrations import get_integrations
        c = get_integrations(actx)["servicenow"]
        return bool(c.get("enabled") and c.get("instance_url") and c.get("username") and c.get("has_password")), "not configured (Administration > Integrations)"
    if agent == "notifier":
        return True, ""  # the dashboard channel always works
    return True, ""


def agent_status(actx: AppContext) -> list[dict]:
    db = actx.db
    since24 = to_iso(utcnow() - timedelta(hours=24))
    stale = to_iso(utcnow() - timedelta(minutes=STALE_RUNNING_MINUTES))
    llm = actx.llm.safe_get()
    out = []
    for a in AGENTS:
        aid = a["id"]
        running = db.one("SELECT COUNT(*) AS n FROM agent_activity WHERE agent=? AND status='running' AND started_at>=?", (aid, stale))["n"]
        last = db.one("SELECT id,status,started_at,finished_at,ms,summary,error,run_id,incident_id FROM agent_activity "
                      "WHERE agent=? AND status!='running' ORDER BY id DESC LIMIT 1", (aid,))
        stats = db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok, "
                       "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, AVG(ms) AS avg_ms "
                       "FROM agent_activity WHERE agent=? AND started_at>=? AND status IN ('ok','failed')", (aid, since24))
        configured, why = _configured(actx, aid)
        if aid == "scheduler":
            alive = bool(actx.scheduler and actx.scheduler.alive())
            status = "disabled" if actx.settings.disable_scheduler else ("running" if alive else "stopped")
            last_tick = actx.scheduler.last_tick if actx.scheduler else None
            out.append({**a, "status": status, "running_now": 1 if status == "running" else 0, "heartbeat": last_tick,
                        "note": "disabled by LOGFIX_DISABLE_SCHEDULER" if status == "disabled" else "",
                        "last": None, "runs_24h": 0, "ok_24h": 0, "failed_24h": 0, "avg_ms": None, "mode": "n/a"})
            continue
        if aid == "collector":
            running = running or (len(actx.scheduler._running) if actx.scheduler else 0)
        if not configured:
            status = "not_configured"
        elif running:
            status = "running"
        elif last and last["status"] == "failed":
            status = "error"
        else:
            status = "idle"
        mode = ("LLM" if llm.is_real else "heuristic (no LLM)") if a["llm"] else "rules"
        out.append({**a, "status": status, "running_now": running, "note": why if not configured else "",
                    "last": last, "runs_24h": stats["n"] or 0, "ok_24h": stats["ok"] or 0, "failed_24h": stats["failed"] or 0,
                    "avg_ms": int(stats["avg_ms"]) if stats["avg_ms"] else None, "mode": mode,
                    "provider": f"{llm.name}/{llm.model}" if a["llm"] else None})
    return out
