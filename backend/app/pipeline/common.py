"""Shared types and helpers for pipeline stages."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..context import AppContext
from ..db import parse_ts, to_iso, utcnow
from ..llm.base import LLMError


@dataclass
class StageOutcome:
    summary: str
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"            # ok | skipped
    degraded: bool = False        # True when a fallback replaced a failed/unavailable LLM


@dataclass
class State:
    """Mutable bag passed through the stages of one pipeline run."""
    run_id: int
    incident_id: int
    user: str | None
    incident: dict
    outputs: dict[str, dict] = field(default_factory=dict)   # stage id -> result


def get_incident(actx: AppContext, iid: int) -> dict:
    inc = actx.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    if not inc:
        raise ValueError("incident not found")
    return inc


def incident_logs(actx: AppContext, inc: dict, limit: int = 200) -> list[dict]:
    return actx.db.query(
        "SELECT id,ts,severity,service,host,message,redactions FROM logs WHERE fingerprint=? AND service IS ? "
        "ORDER BY ts DESC LIMIT ?", (inc["fingerprint"], inc["service"], limit))


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def call_llm_json(actx: AppContext, system: str, user: str, max_tokens: int = 900) -> tuple[dict | None, str | None]:
    """One tool-less model call expected to return a JSON object.
    Returns (data, error). error is set when the model is unreachable/unusable so callers can
    fall back to deterministic logic and label the result as degraded."""
    llm = actx.llm.safe_get()
    if not llm.is_real:
        return None, None   # heuristic provider: callers use their deterministic path (not an error)
    try:
        with actx.investigation_slots:
            resp = llm.complete(system, [{"role": "user", "content": user}], None, max_tokens=max_tokens, temperature=0.2)
    except LLMError as e:
        return None, f"{e.kind}: {e.message}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    d = extract_json(resp.text)
    return (d, None) if d else (None, "model did not return valid JSON")


def minutes_between(a: str | None, b: str | None) -> float:
    ta, tb = parse_ts(a), parse_ts(b)
    return max(0.0, (tb - ta).total_seconds() / 60) if ta and tb else 0.0


def window(inc: dict, before_min: int = 0, after_min: int = 0) -> tuple[str, str]:
    t0, t1 = parse_ts(inc["first_seen"]), parse_ts(inc["last_seen"])
    return to_iso(t0 - timedelta(minutes=before_min)), to_iso(t1 + timedelta(minutes=after_min))
