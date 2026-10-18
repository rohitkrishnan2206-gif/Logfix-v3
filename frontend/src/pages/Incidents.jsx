import { useEffect, useState } from "react";
import PipelineCard from "./Pipeline.jsx";
import { api, openReport } from "../api.js";
import { can, Card, Empty, ErrorBox, Field, fmtTime, LlmBadge, nav, relTime, SevBadge, Spinner, StatusBadge, Trace, useInterval, useLoad } from "../ui.jsx";

export default function Incidents({ user, route }) {
  return route.id ? <Detail id={route.id} user={user} /> : <List />;
}

function List() {
  const [f, setF] = useState({ status: "", severity: "", q: "" });
  const { data, error, loading, reload } = useLoad(() => {
    const p = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => v && p.set(k, v));
    return api("/api/incidents?" + p);
  }, [f.status, f.severity, f.q]);
  useInterval(() => reload(true), 10000);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <div className="page-h"><h1>Incidents</h1><span className="sub">detected automatically from collected logs</span></div>
      <div className="filters">
        <Field label="Status"><select value={f.status} onChange={set("status")}><option value="">All</option><option>open</option><option>acknowledged</option><option>resolved</option></select></Field>
        <Field label="Severity"><select value={f.severity} onChange={set("severity")}><option value="">All</option><option>CRITICAL</option><option>HIGH</option><option>MEDIUM</option></select></Field>
        <Field label="Title contains"><input value={f.q} onChange={set("q")} placeholder="payment" /></Field>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      <Card>
        {loading && !data ? <Spinner /> : !data || data.length === 0 ? <Empty>No incidents. Inject a demo scenario on the Sources page or wait for collection to detect one.</Empty> : (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>ID</th><th>Priority</th><th>Severity</th><th>Status</th><th>Title</th><th className="num">Events</th><th>Last seen</th><th>AI</th></tr></thead>
            <tbody>{data.map((i) => (
              <tr key={i.id}>
                <td className="muted">#{i.id}</td><td>{i.priority ? <span className={`prio ${i.priority}`}>{i.priority}</span> : <span className="muted">-</span>}</td><td><SevBadge sev={i.severity} /></td><td><StatusBadge status={i.status} /></td>
                <td><a href={`#/incidents/${i.id}`}>{i.title}</a></td>
                <td className="num">{i.event_count}</td><td className="nowrap muted">{relTime(i.last_seen)}</td>
                <td>{i.auto_investigated ? <span className="badge ok">investigated</span> : <span className="muted">-</span>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </>
  );
}

function Detail({ id, user }) {
  const { data, error, loading, reload } = useLoad(() => api(`/api/incidents/${id}`), [id]);
  const [active, setActive] = useState(null);
  const [notes, setNotes] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const analyst = can(user, "analyst");

  // Default to the newest investigation (also picks up one started automatically).
  useEffect(() => {
    if (data && active === null && data.investigations.length) setActive(data.investigations[0].id);
  }, [data, active]);

  async function act(fn) {
    setBusy(true);
    setMsg("");
    try { await fn(); } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  const investigate = () => act(async () => {
    const r = await api(`/api/incidents/${id}/investigate`, { method: "POST" });
    setActive(r.investigation_id);
    reload(true);
  });
  const patch = (body) => act(async () => { await api(`/api/incidents/${id}`, { method: "PATCH", body }); await reload(true); });

  if (loading && !data) return <Spinner />;
  if (!data) return <><a href="#/incidents">Back to incidents</a><ErrorBox error={error} onRetry={reload} /></>;
  const running = data.investigations.some((i) => i.status === "running");

  return (
    <>
      <div className="page-h"><a href="#/incidents">Incidents</a><span className="muted">/</span><span>#{data.id}</span></div>
      <Card>
        <div className="row" style={{ marginBottom: 8 }}>
          <SevBadge sev={data.severity} /><StatusBadge status={data.status} />
          <span className="muted">{data.service}</span>
        </div>
        <h1 style={{ marginBottom: 10, wordBreak: "break-word" }}>{data.title}</h1>
        <dl className="kv">
          <dt>Events</dt><dd>{data.event_count}</dd>
          <dt>First seen</dt><dd>{fmtTime(data.first_seen)}</dd>
          <dt>Last seen</dt><dd>{fmtTime(data.last_seen)} ({relTime(data.last_seen)})</dd>
        </dl>
        {msg && <div className="alert alert-err" role="alert">{msg}</div>}
        <div className="row">
          <button className="btn primary" disabled={!analyst || busy || running} onClick={investigate} title={analyst ? "" : "Requires analyst role"}>
            {running ? "Investigating..." : "Investigate with AI"}
          </button>
          <Field label="">
            <select aria-label="Incident status" value={data.status} disabled={!analyst || busy} onChange={(e) => patch({ status: e.target.value })}>
              <option>open</option><option>acknowledged</option><option>resolved</option>
            </select>
          </Field>
          <button className="btn" onClick={() => act(() => openReport(id))}>Open report</button>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <PipelineCard id={id} analyst={analyst} onChange={() => reload(true)} />

      <div style={{ height: 14 }} />
      <Card title="AI investigation" actions={data.investigations.length > 1 && (
        <select aria-label="Earlier investigations" value={active || ""} onChange={(e) => setActive(Number(e.target.value))}>
          {data.investigations.map((i) => <option key={i.id} value={i.id}>#{i.id} {i.status} {relTime(i.created_at)}</option>)}
        </select>
      )}>
        {active ? <InvestigationView key={active} id={active} onDone={() => reload(true)} /> : <Empty>No investigation yet. {analyst ? "Click “Investigate with AI”." : "An analyst can start one."}</Empty>}
      </Card>

      <div style={{ height: 14 }} />
      <Card title="Resolution notes">
        <textarea aria-label="Resolution notes" value={notes ?? data.resolution_notes ?? ""} onChange={(e) => setNotes(e.target.value)} disabled={!analyst} placeholder="What fixed it? This is searched by the AI for similar future incidents." />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" disabled={!analyst || busy || notes === null} onClick={() => patch({ resolution_notes: notes }).then(() => setNotes(null))}>Save notes</button>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <Card title="Sample logs for this pattern">
        {data.sample_logs.length === 0 ? <Empty>No logs retained for this pattern (retention purge).</Empty> : (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>ID</th><th>Time</th><th>Severity</th><th>Host</th><th>Message</th></tr></thead>
            <tbody>{data.sample_logs.map((l) => (
              <tr key={l.id}><td className="muted">#{l.id}</td><td className="nowrap">{fmtTime(l.ts)}</td><td><SevBadge sev={l.severity} /></td><td>{l.host}</td><td className="msg">{l.message}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </>
  );
}

export function InvestigationView({ id, onDone }) {
  const [inv, setInv] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let stop = false, timer;
    async function tick() {
      try {
        const d = await api(`/api/investigations/${id}`);
        if (stop) return;
        setInv(d);
        if (d.status === "running") timer = setTimeout(tick, 2000);
        else if (onDone) onDone();
      } catch (e) {
        if (!stop) setErr(e.message);
      }
    }
    tick();
    return () => { stop = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (err) return <ErrorBox error={err} />;
  if (!inv) return <Spinner />;
  const r = inv.result;
  const mock = inv.provider === "mock";
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <LlmBadge llm={{ provider: inv.provider, model: inv.model, is_real: !mock }} />
        <span className={`badge ${inv.status === "done" ? "ok" : inv.status === "failed" ? "bad" : "warn"}`}>{inv.status}</span>
        <span className="muted">started {relTime(inv.created_at)}{inv.created_by ? ` by ${inv.created_by}` : ""}</span>
      </div>
      {inv.status === "running" && <Spinner label="The agent is gathering evidence" />}
      {inv.status === "failed" && <div className="alert alert-err" role="alert">Investigation failed: {inv.error}</div>}
      {r && (
        <>
          {r.parse_error && <div className="alert alert-warn">The model did not return structured JSON; showing its raw text as the summary.</div>}
          <p className="inv-sum">{r.summary}</p>
          <dl className="kv">
            <dt>Root cause</dt><dd>{r.root_cause || "-"}</dd>
            <dt>Confidence</dt><dd><span className={`badge ${r.confidence === "high" ? "ok" : r.confidence === "medium" ? "warn" : "bad"}`}>{r.confidence || "n/a"}</span></dd>
            <dt>Evidence check</dt>
            <dd>{r.grounding ? `${r.grounding.verified} of ${r.grounding.total} cited logs verified against what the tools returned` : "-"}</dd>
          </dl>
          {r.evidence && r.evidence.length > 0 && (
            <>
              <h3>Evidence</h3>
              <ul className="ev">{r.evidence.map((e, i) => (
                <li key={i} className={e.verified ? "v" : "nv"}>
                  <span className={`badge ${e.verified ? "ok" : "bad"}`}>{e.verified ? "verified" : "not verified"}</span>
                  <span className="muted">log #{e.log_id ?? "?"}</span><span className="mono">{e.note}</span>
                </li>
              ))}</ul>
            </>
          )}
          {r.recommendations && r.recommendations.length > 0 && (<><h3>Recommendations</h3><ul>{r.recommendations.map((x, i) => <li key={i}>{x}</li>)}</ul></>)}
          {r.runbooks && r.runbooks.length > 0 && <div>{r.runbooks.map((x) => <span className="chip" key={x}>{x}</span>)}</div>}
          {r.forced_final && <div className="hint">The agent hit its step limit and was asked to answer with what it had.</div>}
          {r.note && <div className="hint">{r.note}</div>}
          {r.usage && (r.usage.input || r.usage.output) ? <div className="hint">Tokens: {r.usage.input} in, {r.usage.output} out</div> : null}
        </>
      )}
      <Trace trace={inv.trace} open />
    </div>
  );
}
