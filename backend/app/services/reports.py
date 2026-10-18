"""Printable incident report (self-contained HTML; use the browser's Print > Save as PDF)."""
from __future__ import annotations

import html
import json


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def incident_report_html(inc: dict, inv: dict | None, logs: list[dict]) -> str:
    result = json.loads(inv["result"]) if inv and inv.get("result") else {}
    ev = "".join(
        f"<li>log #{_e(e.get('log_id'))} {'(verified)' if e.get('verified') else '(NOT verified)'}: {_e(e.get('note'))}</li>"
        for e in result.get("evidence", [])
    )
    recs = "".join(f"<li>{_e(r)}</li>" for r in result.get("recommendations", []))
    rows = "".join(
        f"<tr><td>{_e(l['id'])}</td><td>{_e(l['ts'])}</td><td>{_e(l['severity'])}</td><td>{_e(l['service'])}</td><td>{_e(l['message'][:300])}</td></tr>"
        for l in logs
    )
    ai = f"{_e(inv.get('provider'))} / {_e(inv.get('model'))}" if inv else "none"
    g = result.get("grounding") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Incident #{_e(inc['id'])}</title>
<style>body{{font:14px system-ui;max-width:900px;margin:2rem auto;color:#111}}h1{{font-size:20px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #ccc;padding:4px;text-align:left}}
.k{{color:#555}}</style></head><body>
<h1>Incident #{_e(inc['id'])}: {_e(inc['title'])}</h1>
<p><span class="k">Severity</span> {_e(inc['severity'])} &middot; <span class="k">Status</span> {_e(inc['status'])} &middot;
<span class="k">Service</span> {_e(inc['service'])} &middot; <span class="k">Events</span> {_e(inc['event_count'])}<br>
<span class="k">First seen</span> {_e(inc['first_seen'])} &middot; <span class="k">Last seen</span> {_e(inc['last_seen'])}</p>
<h2>Analysis</h2><p class="k">Produced by: {ai}. AI output can be wrong; verify against the logs below.</p>
<p><b>Summary:</b> {_e(result.get('summary', 'No investigation run yet.'))}</p>
<p><b>Root cause:</b> {_e(result.get('root_cause', ''))} (confidence: {_e(result.get('confidence', 'n/a'))})</p>
<p><b>Evidence</b> ({_e(g.get('verified', 0))} of {_e(g.get('total', 0))} citations verified)</p><ul>{ev}</ul>
<h2>Recommendations</h2><ul>{recs}</ul>
<h2>Resolution notes</h2><p>{_e(inc.get('resolution_notes') or '')}</p>
<h2>Sample logs</h2><table><tr><th>ID</th><th>Time</th><th>Sev</th><th>Service</th><th>Message</th></tr>{rows}</table>
</body></html>"""
