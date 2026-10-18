"""The 9-stage incident pipeline. Each stage is recorded in agent_activity so the Agents page and the
incident view show what ran, how long it took and what it produced. A failing stage never stops the
later ones: notification and documentation must still happen when the LLM is down."""
from __future__ import annotations

import json
import threading
import traceback
from typing import Callable

from ..context import AppContext
from ..db import now_iso
from ..services import activity
from .classify import stage_classifier
from .common import StageOutcome, State, get_incident
from .notify_doc import stage_documenter, stage_notifier, stage_servicenow
from .rca_reco import stage_rca, stage_recommender
from .stages_basic import stage_data_sanitizer, stage_dedup, stage_log_monitor

STAGES: list[tuple[str, Callable]] = [
    ("log_monitor", stage_log_monitor), ("data_sanitizer", stage_data_sanitizer), ("dedup_engine", stage_dedup),
    ("classifier", stage_classifier), ("rca_correlator", stage_rca), ("recommender", stage_recommender),
    ("notifier", stage_notifier), ("documenter", stage_documenter), ("servicenow", stage_servicenow),
]
# For a duplicate incident these add cost/noise without value: the original already has them.
SKIP_FOR_DUPLICATE = {"rca_correlator", "recommender", "notifier", "documenter", "servicenow"}
# Stages whose output later stages cannot do without. If these fail the run is marked failed but continues.
ORDER = [s for s, _ in STAGES]


def run_pipeline(actx: AppContext, incident_id: int, user: str | None = None, trigger: str = "manual", run_id: int | None = None,
                 only_from: str | None = None) -> int:
    db = actx.db
    inc = get_incident(actx, incident_id)
    llm = actx.llm.safe_get()
    if run_id is None:
        run_id = db.execute("INSERT INTO pipeline_runs(incident_id,status,trigger,created_by,created_at) VALUES(?,?,?,?,?)",
                            (incident_id, "running", trigger, user, now_iso()))
    st = State(run_id=run_id, incident_id=incident_id, user=user, incident=inc)
    failed = 0
    for agent, fn in STAGES:
        if st.incident.get("duplicate_of") and agent in SKIP_FOR_DUPLICATE:
            with activity.Tracked(actx, agent, run_id=run_id, incident_id=incident_id, user=user) as t:
                t.ok(f"Skipped: duplicate of incident #{st.incident['duplicate_of']}", {"duplicate_of": st.incident["duplicate_of"]}, status="skipped")
            continue
        uses_llm = next(a["llm"] for a in activity.AGENTS if a["id"] == agent)
        with activity.Tracked(actx, agent, run_id=run_id, incident_id=incident_id, user=user,
                              provider=llm.name if uses_llm else None, model=llm.model if uses_llm else None) as t:
            try:
                out: StageOutcome = fn(actx, st)
            except Exception as e:  # noqa: BLE001
                failed += 1
                t.fail(f"{type(e).__name__}: {e}", summary="Failed")
                continue
            st.outputs[agent] = out.result
            note = " [fallback: LLM unavailable]" if out.degraded else ""
            t.ok(out.summary + note, out.result, status=out.status)
        st.incident = get_incident(actx, incident_id)
    db.execute("UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?", ("failed" if failed else "done", now_iso(), run_id))
    db.audit(user or "system", "pipeline.run", str(incident_id), {"run": run_id, "failed_stages": failed})
    return run_id


def start_pipeline(actx: AppContext, incident_id: int, user: str | None = None, trigger: str = "manual") -> int:
    """Create the run row and execute in a background thread. Returns the run id."""
    existing = actx.db.one("SELECT id FROM pipeline_runs WHERE incident_id=? AND status='running'", (incident_id,))
    if existing:
        return existing["id"]
    run_id = actx.db.execute("INSERT INTO pipeline_runs(incident_id,status,trigger,created_by,created_at) VALUES(?,?,?,?,?)",
                             (incident_id, "running", trigger, user, now_iso()))

    def work():
        try:
            run_pipeline(actx, incident_id, user, trigger, run_id)
        except Exception:  # noqa: BLE001
            actx.db.execute("UPDATE pipeline_runs SET status='failed', finished_at=? WHERE id=?", (now_iso(), run_id))
            actx.db.audit("system", "pipeline.crash", str(incident_id), traceback.format_exc()[-500:])

    threading.Thread(target=work, daemon=True, name=f"pipeline-{run_id}").start()
    return run_id
