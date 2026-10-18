import { useState } from "react";
import { api } from "../api.js";
import { Card, Empty, ErrorBox, fmtTime, LlmBadge, relTime, Spinner, useInterval, useLoad } from "../ui.jsx";

const LABEL = { running: "Running", idle: "Idle", error: "Error", not_configured: "Not configured", disabled: "Disabled", stopped: "Stopped" };
const CLS = { running: "ok", idle: "", error: "bad", not_configured: "warn", disabled: "warn", stopped: "bad" };
const KIND = { pipeline: "Incident pipeline", service: "Background service", interactive: "On demand" };

function ms(v) { return v == null ? "-" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`; }

function StatusDot({ status }) {
  return <span className={`sdot s-${status}`} aria-hidden="true" />;
}

export default function Agents() {
  const { data, error, loading, reload } = useLoad(() => api("/api/agents"));
  const [filter, setFilter] = useState("");
  const act = useLoad(() => api("/api/agents/activity?limit=60" + (filter ? `&agent=${filter}` : "")), [filter]);
  useInterval(() => { reload(true); act.reload(true); }, 3000);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorBox error={error} onRetry={reload} />;
  const c = data.counts;
  const groups = ["pipeline", "service", "interactive"];
  return (
    <>
      <div className="page-h"><h1>Agents</h1><span className="sub">live status of every agent; refreshes every 3 seconds</span><span className="right"><LlmBadge llm={data.llm} /></span></div>
      <ErrorBox error={error} />
      <div className="statrow">
        <div className="stat"><b>{c.running || 0}</b><span>running now</span></div>
        <div className="stat"><b>{c.idle || 0}</b><span>idle (ready)</span></div>
        <div className={`stat ${c.error ? "bad" : ""}`}><b>{c.error || 0}</b><span>last run failed</span></div>
        <div className={`stat ${(c.not_configured || 0) + (c.disabled || 0) + (c.stopped || 0) ? "warn" : ""}`}><b>{(c.not_configured || 0) + (c.disabled || 0) + (c.stopped || 0)}</b><span>need attention</span></div>
      </div>
      {!data.llm.is_real && <div className="alert alert-warn">No AI model is configured, so agents marked “LLM” run in heuristic mode (fixed rules, not AI). Configure Bedrock or Azure OpenAI in Administration &rsaquo; AI model.</div>}
      {data.running_pipelines.length > 0 && (
        <Card title="Pipelines running now">
          {data.running_pipelines.map((r) => (
            <div className="row" key={r.id}><span className="sdot s-running" /> <a href={`#/incidents/${r.incident_id}`}>Incident #{r.incident_id}</a><span className="muted">{r.title}</span><span className="muted">started {relTime(r.created_at)} ({r.trigger})</span></div>
          ))}
        </Card>
      )}
      {groups.map((g) => (
        <div key={g} style={{ marginTop: 14 }}>
          <Card title={KIND[g]}>
            <div className="tbl-wrap"><table className="tbl agents">
              <thead><tr><th>Agent</th><th>Status</th><th>Mode</th><th>Last run</th><th className="num">24h runs</th><th className="num">Failed</th><th className="num">Avg time</th></tr></thead>
              <tbody>{data.agents.filter((a) => a.kind === g).map((a) => (
                <tr key={a.id}>
                  <td><b>{a.name}</b><div className="muted small">{a.desc}</div></td>
                  <td><span className={`badge ${CLS[a.status]}`}><StatusDot status={a.status} />{LABEL[a.status]}{a.running_now > 1 ? ` (${a.running_now})` : ""}</span>{a.note && <div className="muted small">{a.note}</div>}</td>
                  <td className="small">{a.mode}</td>
                  <td className="small">{a.id === "scheduler" ? (a.heartbeat ? <>heartbeat {relTime(a.heartbeat)}</> : "-") : a.last ? (
                    <><span className={a.last.status === "failed" ? "bad-t" : ""}>{relTime(a.last.finished_at || a.last.started_at)}</span><div className="muted">{(a.last.error || a.last.summary || "").slice(0, 90)}</div></>
                  ) : <span className="muted">never run</span>}</td>
                  <td className="num">{a.runs_24h}</td>
                  <td className="num">{a.failed_24h ? <span className="bad-t">{a.failed_24h}</span> : 0}</td>
                  <td className="num">{ms(a.avg_ms)}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </Card>
        </div>
      ))}
      <div style={{ height: 14 }} />
      <Card title="Activity log" actions={
        <select aria-label="Filter by agent" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All agents</option>
          {data.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>}>
        {act.loading && !act.data ? <Spinner /> : !act.data || act.data.length === 0 ? <Empty>No agent activity yet. Run the pipeline on an incident or inject a demo scenario.</Empty> : (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>Time</th><th>Agent</th><th>Status</th><th>Result</th><th>Incident</th><th className="num">Time taken</th></tr></thead>
            <tbody>{act.data.map((r) => (
              <tr key={r.id}>
                <td className="nowrap">{fmtTime(r.started_at)}</td>
                <td>{r.agent_name}</td>
                <td><span className={`badge ${r.status === "ok" ? "ok" : r.status === "failed" ? "bad" : r.status === "running" ? "warn" : ""}`}>{r.status}</span></td>
                <td className="small">{r.error ? <span className="bad-t">{r.error}</span> : r.summary}</td>
                <td>{r.incident_id ? <a href={`#/incidents/${r.incident_id}`}>#{r.incident_id}</a> : "-"}</td>
                <td className="num">{ms(r.ms)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </>
  );
}
