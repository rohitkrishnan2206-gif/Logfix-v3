import { useEffect, useState } from "react";
import { api, download } from "../api.js";
import { Card, Empty, ErrorBox, relTime, Spinner } from "../ui.jsx";

const ICON = { ok: "✓", failed: "✗", skipped: "–", running: "●", pending: "·" };

function ms(v) { return v == null ? "-" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`; }

export default function PipelineCard({ id, analyst, onChange }) {
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let stop = false, timer;
    async function load() {
      try {
        const v = await api(`/api/incidents/${id}/pipeline`);
        if (stop) return;
        setView(v);
        if (v.run && v.run.status === "running") timer = setTimeout(load, 1500);
        else if (v.run) onChange && onChange();
      } catch (e) { if (!stop) setErr(e.message); }
    }
    load();
    return () => { stop = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tick]);

  async function startRun() {
    setErr(""); setBusy("run");
    try { await api(`/api/incidents/${id}/pipeline`, { method: "POST" }); setTick((t) => t + 1); } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  async function dl(fmt) {
    setErr(""); setBusy(fmt);
    try { await download(`/api/incidents/${id}/resolution.${fmt}`, `incident-${id}-resolution-guide.${fmt}`); } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  async function ticket() {
    setErr(""); setBusy("ticket");
    try { await api(`/api/incidents/${id}/ticket`, { method: "POST" }); setTick((t) => t + 1); } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }

  if (!view) return <Card title="Agent pipeline">{err ? <ErrorBox error={err} /> : <Spinner />}</Card>;
  const inc = view.incident, run = view.run;
  const running = run && run.status === "running";
  const out = (a) => (run ? run.stages.find((s) => s.agent === a)?.result : null) || {};
  const cls = out("classifier"), reco = out("recommender"), rca = out("rca_correlator");
  const hasGuide = run && run.stages.find((s) => s.agent === "documenter")?.status === "ok";
  const eta = reco.eta || (inc.eta_min ? { min: inc.eta_min, max: inc.eta_max } : null);

  return (
    <Card title="Agent pipeline" actions={
      <button className="btn primary" disabled={!analyst || running || busy === "run"} onClick={startRun} title={analyst ? "" : "Requires analyst role"}>
        {running ? "Running..." : run ? "Run again" : "Run pipeline"}
      </button>}>
      <ErrorBox error={err} />
      {!run ? <Empty>The 9 agents have not run for this incident yet. New incidents run automatically when “Run the agent pipeline automatically” is on (Administration &rsaquo; Detection).</Empty> : (
        <>
          <div className="pipe-head">
            {inc.priority && <span className={`prio ${inc.priority}`}>{inc.priority}</span>}
            {inc.score != null && <span><b>{inc.score}</b>/100</span>}
            {cls.category_label && <span className="badge">{cls.category_label}</span>}
            {eta && <span>Estimated resolution: <b>{eta.min}-{eta.max} min</b></span>}
            {inc.duplicate_of && <span className="badge warn">Duplicate of <a href={`#/incidents/${inc.duplicate_of}`}>#{inc.duplicate_of}</a></span>}
            {inc.itsm_ref && <span className="badge ok">{inc.itsm_url ? <a href={inc.itsm_url} target="_blank" rel="noopener noreferrer">{inc.itsm_ref}</a> : inc.itsm_ref}</span>}
            <span className="muted">run #{run.id} {run.status}, {relTime(run.created_at)}</span>
          </div>
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th /><th>Agent</th><th>Result</th><th className="num">Time</th></tr></thead>
            <tbody>{run.stages.map((s) => (
              <tr key={s.agent} onClick={() => s.result && setOpen(open === s.agent ? null : s.agent)} style={{ cursor: s.result ? "pointer" : "default" }}>
                <td className={`stage-state stage-${s.status}`} title={s.status}>{ICON[s.status]}</td>
                <td className="nowrap"><b>{s.name}</b></td>
                <td className="small">
                  {s.status === "running" ? <span className="muted">working...</span> : s.status === "pending" ? <span className="muted">waiting</span> :
                    s.error ? <span className="bad-t">Failed: {s.error}</span> : s.summary}
                  {open === s.agent && s.result && <pre className="tprev" style={{ maxHeight: 260 }}>{JSON.stringify(s.result, null, 2).slice(0, 4000)}</pre>}
                </td>
                <td className="num">{ms(s.ms)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <div className="hint">Click a row to see what that agent produced.</div>

          {cls.factors && (
            <details style={{ marginTop: 10 }}><summary>Why {cls.priority}? (score breakdown)</summary>
              {cls.factors.map((f) => <div className="factor" key={f.name}><span>{f.name}</span><b>+{f.points}</b><span className="muted">{f.detail}</span></div>)}
            </details>
          )}

          {reco.immediate_actions && (
            <>
              <h3>Fix it now {reco.source === "llm" ? "" : <span className="muted small">(from {reco.source === "runbook" ? "your runbook" : "built-in playbook"})</span>}</h3>
              <ol className="steps-list">{reco.immediate_actions.map((a, i) => (
                <li key={i}><b>{a.step}</b><span className={`badge risk ${a.risk === "high" ? "bad" : a.risk === "medium" ? "warn" : "ok"}`}>{a.risk} risk</span>
                  <div className="muted small">{a.detail}</div></li>
              ))}</ol>
              {reco.auto_remediation && reco.auto_remediation.length > 0 && (
                <>
                  <h3>Auto-remediation plans</h3>
                  <div className="alert alert-warn">Prepared for a person to approve and run. LogFix does not execute these.</div>
                  {reco.auto_remediation.map((p, i) => (
                    <details key={i} open><summary><b>{p.title}</b></summary>
                      <ol className="steps-list">{p.steps.map((s, j) => <li key={j}>{s}</li>)}</ol>
                      {p.rollback && <div className="muted small">Undo: {p.rollback}</div>}
                    </details>
                  ))}
                </>
              )}
              {reco.verification && <><h3>Confirm it is fixed</h3><ul>{reco.verification.map((v, i) => <li key={i}>{v}</li>)}</ul></>}
              {reco.rollback && <><h3>If it gets worse</h3><ul>{reco.rollback.map((v, i) => <li key={i}>{v}</li>)}</ul></>}
              {eta && <div className="hint">Estimate basis: {reco.eta?.basis || "history"}.</div>}
            </>
          )}
          {rca.root_cause && <div className="hint">Root cause (RCA agent): {rca.root_cause}</div>}

          <h3>Resolution guide</h3>
          {hasGuide ? (
            <div className="dl-row">
              <button className="btn primary" disabled={!!busy} onClick={() => dl("pdf")}>{busy === "pdf" ? "Preparing..." : "Download PDF"}</button>
              <button className="btn" disabled={!!busy} onClick={() => dl("txt")}>Download text (.txt)</button>
              <button className="btn" disabled={!!busy} onClick={() => dl("md")}>Markdown (.md)</button>
            </div>
          ) : <div className="muted">Available once the Documenter agent has finished.</div>}

          {!inc.itsm_ref && (
            <div style={{ marginTop: 12 }}><button className="btn" disabled={!analyst || !!busy || running} onClick={ticket}>{busy === "ticket" ? "Creating..." : "Create ServiceNow ticket"}</button>
              <span className="muted small"> uses the ServiceNow settings in Administration &rsaquo; Integrations</span></div>
          )}
          {view.notifications.length > 0 && (
            <>
              <h3>Notifications sent</h3>
              <div className="tbl-wrap"><table className="tbl"><tbody>{view.notifications.map((n, i) => (
                <tr key={i}><td>{n.channel}</td><td><span className={`badge ${n.status === "sent" ? "ok" : n.status === "failed" ? "bad" : ""}`}>{n.status}</span></td><td className="small muted">{n.detail}</td><td className="small muted">{relTime(n.created_at)}</td></tr>
              ))}</tbody></table></div>
            </>
          )}
        </>
      )}
    </Card>
  );
}
