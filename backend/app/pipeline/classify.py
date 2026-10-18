"""Category detection and the transparent 0-100 priority score (Classifier agent)."""
from __future__ import annotations

import math
import re
from datetime import timedelta

from ..context import AppContext
from ..db import parse_ts, to_iso, utcnow
from .common import StageOutcome, State, incident_logs, minutes_between, window

# Order = tie-break priority. Keywords are matched against the incident's own log messages.
CATEGORY_RULES: list[tuple[str, str, re.Pattern]] = [
    ("resource_exhaustion", "Resource Exhaustion", re.compile(
        r"pool exhausted|connection pool|too many connections|out ?of ?memory|outofmemory|oomkilled|heap|no space left|"
        r"enospc|disk (usage|full)|\bcpu\b|memory (usage|pressure)|quota exceeded|limit (reached|exceeded)|exhaust", re.I)),
    ("availability", "Service Availability", re.compile(
        r"connection refused|unreachable|service unavailable|\b50[23]\b|crashloop|unhealthy|not responding|health ?check|restarting", re.I)),
    ("auth_failure", "Authentication / Authorization", re.compile(
        r"\b40[13]\b|unauthori[sz]ed|forbidden|token (expired|invalid)|invalid signature|access denied|authentication failed", re.I)),
    ("data_store", "Data Store", re.compile(
        r"deadlock|replication lag|could not write|constraint|transaction|\bwal\b|sql|database write", re.I)),
    ("network", "Network / TLS", re.compile(r"\bdns\b|\btls\b|\bssl\b|certificate|handshake|connection reset|packet loss", re.I)),
    ("dependency_failure", "Dependency Failure", re.compile(
        r"upstream|timed out|timeout|gateway timeout|\b504\b|paymenttimeout|downstream|dependency", re.I)),
    ("performance_degradation", "Performance Degradation", re.compile(r"\bslow\b|latency|gc pause|degrad|took \d+", re.I)),
    ("configuration", "Configuration", re.compile(r"misconfig|invalid config|missing (env|variable|setting)", re.I)),
]
LABELS = {c: l for c, l, _ in CATEGORY_RULES} | {"unknown": "Unclassified"}

BUSINESS_CRITICAL = re.compile(r"payment|checkout|billing|order|auth|login|gateway|database|postgres|mysql|\bdb\b", re.I)
DEPLOY_RE = re.compile(r"deploy(ment|ed)?\b|rollout|release|helm upgrade|version \S+ (started|completed)", re.I)

SEV_BASE = {"CRITICAL": 45, "HIGH": 30, "MEDIUM": 15, "LOW": 5, "INFO": 0}


def detect_category(messages: list[str]) -> str:
    best, best_n = "unknown", 0
    for cat, _label, rx in CATEGORY_RULES:
        n = sum(1 for m in messages if rx.search(m))
        if n > best_n:  # strict >: earlier rules win ties
            best, best_n = cat, n
    return best


def priority_for(score: int) -> str:
    return "P1" if score >= 80 else "P2" if score >= 60 else "P3" if score >= 35 else "P4"


def find_recent_deploy(actx: AppContext, inc: dict) -> dict | None:
    """A deployment-looking log in the same source within 15 minutes before the incident began."""
    t0 = parse_ts(inc["first_seen"])
    rows = actx.db.query(
        "SELECT id,ts,service,message FROM logs WHERE source_id IS ? AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT 400",
        (inc["source_id"], to_iso(t0 - timedelta(minutes=15)), to_iso(t0)))
    for r in rows:
        if DEPLOY_RE.search(r["message"]):
            return r
    return None


def classify(actx: AppContext, inc: dict) -> dict:
    logs = incident_logs(actx, inc, 60)
    cat = detect_category([l["message"] for l in logs]) if logs else "unknown"
    factors = []

    def add(name, pts, detail):
        factors.append({"name": name, "points": pts, "detail": detail})

    add("severity", SEV_BASE.get(inc["severity"], 0), f"{inc['severity']} severity")
    vol = min(15, round(3 * math.log2(1 + max(inc["event_count"], 0))))
    add("volume", vol, f"{inc['event_count']} events")

    w0, w1 = window(inc, 2, 2)
    svcs = [r["service"] for r in actx.db.query(
        "SELECT DISTINCT service FROM logs WHERE ts>=? AND ts<=? AND severity IN ('HIGH','CRITICAL') AND service IS NOT NULL", (w0, w1))]
    blast = min(15, 5 * max(0, len(svcs) - 1))
    add("blast radius", blast, f"{len(svcs)} service(s) erroring at the same time: {', '.join(sorted(svcs)[:6])}" if svcs else "1 service")

    now = utcnow()
    last10 = actx.db.one("SELECT COUNT(*) AS n FROM logs WHERE fingerprint=? AND service IS ? AND ts>=?",
                         (inc["fingerprint"], inc["service"], to_iso(now - timedelta(minutes=10))))["n"]
    prev10 = actx.db.one("SELECT COUNT(*) AS n FROM logs WHERE fingerprint=? AND service IS ? AND ts>=? AND ts<?",
                         (inc["fingerprint"], inc["service"], to_iso(now - timedelta(minutes=20)), to_iso(now - timedelta(minutes=10))))["n"]
    if last10 >= 3 and last10 > prev10 * 1.5:
        add("trend", 10, f"accelerating: {last10} in last 10 min vs {prev10} before")
    elif last10 > 0:
        add("trend", 5, f"still ongoing: {last10} in last 10 min")
    else:
        add("trend", 0, "no events in the last 10 minutes")

    biz = BUSINESS_CRITICAL.search(f"{inc['service'] or ''} {inc['title']}")
    add("business-critical path", 10 if biz else 0, f"matches '{biz.group(0)}'" if biz else "no business-critical keyword")
    ongoing_min = minutes_between(inc["first_seen"], inc["last_seen"])
    add("duration", 5 if ongoing_min >= 15 else 0, f"{ongoing_min:.0f} min so far")

    score = max(0, min(100, sum(f["points"] for f in factors)))
    deploy = find_recent_deploy(actx, inc)
    return {"score": score, "priority": priority_for(score), "category": cat, "category_label": LABELS[cat],
            "factors": factors, "recent_deploy": {"log_id": deploy["id"], "ts": deploy["ts"], "message": deploy["message"][:200]} if deploy else None,
            "services_affected": sorted(svcs), "severity": inc["severity"]}


def stage_classifier(actx: AppContext, st: State) -> StageOutcome:
    r = classify(actx, st.incident)
    actx.db.execute("UPDATE incidents SET priority=?, score=?, category=? WHERE id=?", (r["priority"], r["score"], r["category"], st.incident_id))
    st.incident.update(priority=r["priority"], score=r["score"], category=r["category"])
    return StageOutcome(f"{r['priority']} {r['severity']}, score {r['score']}/100, category: {r['category']}", r)
