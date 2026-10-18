import { useState } from "react";
import { api } from "../api.js";
import { Card, CloudBadge, Empty, ErrorBox, Field, fmtTime, SevBadge, Spinner, takeHandoff, useInterval, useLoad } from "../ui.jsx";

const PAGE = 50;
const SEV = [
  ["", "All severities"],
  ["HIGH,CRITICAL", "Errors (HIGH+)"],
  ["MEDIUM,HIGH,CRITICAL", "Warnings and up"],
  ["CRITICAL", "CRITICAL"], ["HIGH", "HIGH"], ["MEDIUM", "MEDIUM"], ["LOW", "LOW"], ["INFO", "INFO"],
];
const WINDOWS = [[15, "15 min"], [60, "1 hour"], [360, "6 hours"], [1440, "24 hours"], [10080, "7 days"]];

export default function Logs() {
  const [form, setForm] = useState(() => {
    const q = takeHandoff("logsQ");
    return { q, severity: "", service: "", source_id: "", minutes: q ? 1440 : 60 };
  });
  const [applied, setApplied] = useState(form);
  const [offset, setOffset] = useState(0);
  const [auto, setAuto] = useState(false);
  const services = useLoad(() => api("/api/logs/services"));
  const sources = useLoad(() => api("/api/sources"));
  const { data, error, loading, reload } = useLoad(() => {
    const p = new URLSearchParams({ minutes: applied.minutes, limit: PAGE, offset });
    if (applied.q) p.set("q", applied.q);
    if (applied.severity) p.set("severity", applied.severity);
    if (applied.service) p.set("service", applied.service);
    if (applied.source_id) p.set("source_id", applied.source_id);
    return api("/api/logs?" + p);
  }, [applied, offset]);
  useInterval(() => reload(true), 5000, auto);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const srcName = (id) => (sources.data || []).find((s) => s.id === id);

  return (
    <>
      <div className="page-h"><h1>Logs</h1><span className="sub">collected and redacted</span></div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(form); }}>
        <Field label="Search (all words must match)"><input value={form.q} onChange={set("q")} placeholder="timeout payment" /></Field>
        <Field label="Severity"><select value={form.severity} onChange={set("severity")}>{SEV.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <Field label="Service"><select value={form.service} onChange={set("service")}><option value="">All services</option>{(services.data || []).map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Source"><select value={form.source_id} onChange={set("source_id")}><option value="">All sources</option>{(sources.data || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Time window"><select value={form.minutes} onChange={set("minutes")}>{WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <button className="btn primary">Search</button>
        <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-refresh (5s)</label>
      </form>
      <ErrorBox error={error} onRetry={reload} />
      <Card title={data ? `${data.total} matching logs` : "Logs"}>
        {loading && !data ? <Spinner /> : !data || data.logs.length === 0 ? <Empty>No logs match. Try a wider time window, or collect from a source.</Empty> : (
          <>
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>ID</th><th>Time</th><th>Severity</th><th>Service</th><th>Source</th><th>Message</th></tr></thead>
              <tbody>{data.logs.map((l) => {
                const s = srcName(l.source_id);
                return (
                  <tr key={l.id}>
                    <td className="muted">#{l.id}</td><td className="nowrap">{fmtTime(l.ts)}</td>
                    <td><SevBadge sev={l.severity} /></td><td>{l.service}</td>
                    <td>{s ? <CloudBadge cloud={s.cloud} /> : ""}</td><td className="msg">{l.message}</td>
                  </tr>
                );
              })}</tbody>
            </table></div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <span className="muted">{offset + 1}-{Math.min(offset + PAGE, data.total)} of {data.total}</span>
              <button className="btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
