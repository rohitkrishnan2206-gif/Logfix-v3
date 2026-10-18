"""ServiceNow Table API client (create incident). Basic auth; use a dedicated integration user."""
from __future__ import annotations

import httpx

from ..redact import redact

# ServiceNow: 1=High, 2=Medium, 3=Low. Priority is computed by ServiceNow from impact+urgency.
IMPACT_URGENCY = {"P1": (1, 1), "P2": (1, 2), "P3": (2, 2), "P4": (3, 3)}


def create_incident(cfg: dict, inc: dict, summary: str, description: str, base_url: str = "",
                    transport: httpx.BaseTransport | None = None) -> dict:
    prio = inc.get("priority") or "P3"
    impact, urgency = IMPACT_URGENCY.get(prio, (2, 2))
    body = {
        "short_description": redact(f"[LogFix {prio}] {inc['title']}")[:160],
        "description": redact(description)[:4000],
        "impact": impact, "urgency": urgency,
        "category": "software", "caller_id": cfg.get("username", ""),
        "work_notes": redact(summary)[:2000],
    }
    if base_url:
        body["work_notes"] += f"\nLogFix incident: {base_url}/#incidents/{inc['id']}"
    url = cfg["instance_url"].rstrip("/") + "/api/now/table/incident"
    with httpx.Client(transport=transport, timeout=20) as c:
        r = c.post(url, json=body, auth=(cfg["username"], cfg["password"]),
                   headers={"Accept": "application/json", "Content-Type": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"ServiceNow returned HTTP {r.status_code}: {r.text[:200]}")
    res = (r.json() or {}).get("result") or {}
    number = res.get("number")
    if not number:
        raise RuntimeError("ServiceNow response had no incident number")
    return {"number": number, "sys_id": res.get("sys_id"), "url": f"{cfg['instance_url'].rstrip('/')}/nav_to.do?uri=incident.do?sys_id={res.get('sys_id')}",
            "impact": impact, "urgency": urgency, "priority_label": prio}
