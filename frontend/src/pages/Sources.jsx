import { useState } from "react";
import { api } from "../api.js";
import { can, Card, CloudBadge, Empty, ErrorBox, Field, relTime, Spinner, useLoad } from "../ui.jsx";

const INTERVALS = [[0, "Manual only"], [30, "Every 30 seconds"], [60, "Every 1 minute"], [300, "Every 5 minutes"], [600, "Every 10 minutes"], [900, "Every 15 minutes"], [1800, "Every 30 minutes"]];

// Config + secret fields per cloud (mirrors backend connectors and SECRET_FIELDS).
const SCHEMA = {
  aws: {
    config: [
      { k: "region", l: "Region", ph: "us-east-1" },
      { k: "log_groups", l: "Log groups (one per line)", area: true, list: true, ph: "/logfix-ai/test-logs", full: true },
      { k: "filter_pattern", l: "CloudWatch filter pattern (optional)", ph: "ERROR" },
    ],
    secrets: [
      { k: "access_key_id", l: "Access key ID" },
      { k: "secret_access_key", l: "Secret access key" },
      { k: "session_token", l: "Session token (temporary credentials)" },
    ],
    note: "Leave the keys blank to use the server's IAM role or default AWS credentials (recommended on EC2). Grant logs:FilterLogEvents, logs:DescribeLogGroups and logs:DescribeLogStreams only.",
  },
  azure: {
    config: [
      { k: "workspace_id", l: "Log Analytics workspace ID" },
      { k: "tenant_id", l: "Tenant ID" },
      { k: "client_id", l: "App (client) ID" },
      { k: "computer", l: "Computer / VM name (optional)", ph: "vm-logfix-demo" },
      { k: "kql", l: "Custom KQL (optional)", area: true, full: true, ph: "Use {since}, {until}, {limit}. Must return TimeGenerated, Computer, Message, Level, Proc." },
    ],
    secrets: [{ k: "client_secret", l: "Client secret" }],
    note: "The app registration needs the Log Analytics Reader role on the workspace.",
  },
  gcp: {
    config: [
      { k: "project_id", l: "Project ID" },
      { k: "filter", l: "Extra Cloud Logging filter (optional)", ph: 'resource.type="gce_instance"', full: true },
    ],
    secrets: [{ k: "service_account_json", l: "Service account JSON key", area: true }],
    note: "The service account needs only the Logs Viewer role.",
  },
  demo: { config: [], secrets: [], note: "Simulated data. No cloud account is used." },
};

function emptyForm(cloud = "demo") {
  return { id: null, name: "", cloud, config: {}, secrets: {}, interval_seconds: 0, enabled: true, secrets_set: [] };
}

export default function Sources({ user }) {
  const { data, error, loading, reload } = useLoad(() => api("/api/sources"));
  const scen = useLoad(() => api("/api/demo/scenarios"));
  const [form, setForm] = useState(null);
  const [res, setRes] = useState({});
  const [busy, setBusy] = useState({});
  const admin = can(user, "admin"), analyst = can(user, "analyst");

  const setR = (id, v) => setRes((r) => ({ ...r, [id]: v }));
  async function run(id, label, fn) {
    setBusy((b) => ({ ...b, [id]: label }));
    setR(id, null);
    try { setR(id, await fn()); } catch (e) { setR(id, { error: e.message }); } finally {
      setBusy((b) => ({ ...b, [id]: null }));
      reload(true);
    }
  }
  const test = (s) => run(s.id, "test", async () => { const r = await api(`/api/sources/${s.id}/test`, { method: "POST" }); return r.ok ? { ok: "Connection works." } : { error: `${r.kind}: ${r.error}` }; });
  const collect = (s) => run(s.id, "collect", async () => { const r = await api(`/api/sources/${s.id}/collect`, { method: "POST" }); return r.error ? { error: r.error } : { ok: `Fetched ${r.fetched}, stored ${r.inserted} new logs, ${r.incidents.length} new incident(s).`, link: r.incidents.length }; });
  const inject = (s, sc) => run(s.id, "inject", async () => { const r = await api(`/api/sources/${s.id}/demo/inject`, { method: "POST", body: { scenario: sc } }); return r.error ? { error: r.error } : { ok: `Scenario "${sc}" started. Stored ${r.inserted} logs, ${r.incidents.length} incident(s) detected.`, link: r.incidents.length }; });
  const del = async (s) => {
    if (!window.confirm(`Delete source "${s.name}" and all of its stored logs?`)) return;
    run(s.id, "delete", async () => { await api(`/api/sources/${s.id}`, { method: "DELETE" }); return { ok: "Deleted." }; });
  };

  return (
    <>
      <div className="page-h">
        <h1>Sources</h1><span className="sub">read-only connections to cloud logs</span>
        <button className="btn primary right" disabled={!admin} title={admin ? "" : "Requires admin role"} onClick={() => setForm(emptyForm())}>Add source</button>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {form && <SourceForm key={form.id || "new"} init={form} onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(true); }} />}
      {loading && !data ? <Spinner /> : !data || data.length === 0 ? <Empty>No sources yet.</Empty> : (
        <div className="stack">{data.map((s) => (
          <Card key={s.id}>
            <div className="row">
              <h3 style={{ margin: 0 }}>{s.name}</h3><CloudBadge cloud={s.cloud} />
              {!s.enabled && <span className="badge warn">disabled</span>}
              {s.last_status && <span className={`badge ${s.last_status === "ok" ? "ok" : "bad"}`}>{s.last_status}</span>}
              <span className="muted">{INTERVALS.find(([v]) => v === s.interval_seconds)?.[1] || `${s.interval_seconds}s`}, last collected {relTime(s.last_collected_at)}</span>
            </div>
            {s.last_error && <div className="alert alert-err" style={{ marginTop: 8 }}>{s.last_error}</div>}
            <div className="row" style={{ marginTop: 10 }}>
              {s.secrets_set.map((k) => <span key={k} className="chip set">{k}: set</span>)}
              <button className="btn sm" disabled={!analyst || !!busy[s.id]} onClick={() => test(s)}>{busy[s.id] === "test" ? "Testing..." : "Test"}</button>
              <button className="btn sm" disabled={!analyst || !!busy[s.id]} onClick={() => collect(s)}>{busy[s.id] === "collect" ? "Collecting..." : "Collect now"}</button>
              <button className="btn sm" disabled={!admin} onClick={() => setForm({ ...emptyForm(s.cloud), ...s, secrets: {} })}>Edit</button>
              <button className="btn sm danger" disabled={!admin || !!busy[s.id]} onClick={() => del(s)}>Delete</button>
            </div>
            {s.cloud === "demo" && (
              <div className="row" style={{ marginTop: 10 }}>
                <span className="muted">Demo scenario:</span>
                {Object.entries(scen.data || {}).map(([k, desc]) => (
                  <button key={k} className="btn sm" title={desc} disabled={!analyst || !!busy[s.id]} onClick={() => inject(s, k)}>{k}</button>
                ))}
                {s.config.scenario && <span className="muted">active: {s.config.scenario}</span>}
              </div>
            )}
            {res[s.id] && (
              <div className={`alert ${res[s.id].error ? "alert-err" : "alert-ok"}`} style={{ marginTop: 10, marginBottom: 0 }} role="status">
                <span>{res[s.id].error || res[s.id].ok}</span>
                {res[s.id].link ? <a href="#/incidents">View incidents</a> : null}
              </div>
            )}
          </Card>
        ))}</div>
      )}
    </>
  );
}

function SourceForm({ init, onClose, onSaved }) {
  const editing = !!init.id;
  const [f, setF] = useState(() => {
    const cfg = { ...init.config };
    // list fields are edited as text
    Object.entries(SCHEMA[init.cloud].config).forEach(([, fd]) => { if (fd.list && Array.isArray(cfg[fd.k])) cfg[fd.k] = cfg[fd.k].join("\n"); });
    return { ...init, config: cfg };
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const schema = SCHEMA[f.cloud];
  const setCfg = (k, v) => setF({ ...f, config: { ...f.config, [k]: v } });
  const setSec = (k, v) => setF({ ...f, secrets: { ...f.secrets, [k]: v } });

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const config = { ...f.config };
    schema.config.forEach((fd) => {
      if (fd.list) config[fd.k] = String(config[fd.k] || "").split("\n").map((x) => x.trim()).filter(Boolean);
      else if (config[fd.k] === "") delete config[fd.k];
    });
    const secrets = Object.fromEntries(Object.entries(f.secrets).filter(([, v]) => v));
    try {
      if (editing) await api(`/api/sources/${f.id}`, { method: "PATCH", body: { name: f.name, config, secrets, interval_seconds: Number(f.interval_seconds), enabled: f.enabled } });
      else await api("/api/sources", { method: "POST", body: { name: f.name, cloud: f.cloud, config, secrets, interval_seconds: Number(f.interval_seconds), enabled: f.enabled } });
      onSaved();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  const input = (fd, val, onChange, type = "text") => fd.area
    ? <textarea value={val} onChange={(e) => onChange(e.target.value)} placeholder={fd.ph} rows={fd.list ? 3 : 4} />
    : <input type={type} value={val} onChange={(e) => onChange(e.target.value)} placeholder={fd.ph} autoComplete="off" />;

  return (
    <form className="card stack" onSubmit={save} style={{ marginBottom: 14 }}>
      <h2>{editing ? `Edit ${init.name}` : "Add source"}</h2>
      {err && <div className="alert alert-err" role="alert">{err}</div>}
      <div className="form-grid">
        <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
        <Field label="Cloud">
          <select value={f.cloud} disabled={editing} onChange={(e) => setF({ ...f, cloud: e.target.value, config: {}, secrets: {} })}>
            <option value="aws">AWS CloudWatch Logs</option><option value="azure">Azure Log Analytics</option><option value="gcp">GCP Cloud Logging</option><option value="demo">Demo (simulated)</option>
          </select>
        </Field>
        <Field label="Collection schedule">
          <select value={f.interval_seconds} onChange={(e) => setF({ ...f, interval_seconds: e.target.value })}>{INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        </Field>
        <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Enabled</label>
        {schema.config.map((fd) => (
          <Field key={fd.k} label={fd.l} full={fd.full}>{input(fd, f.config[fd.k] ?? "", (v) => setCfg(fd.k, v))}</Field>
        ))}
        {schema.secrets.map((fd) => (
          <Field key={fd.k} label={`${fd.l} (write-only)`} hint={init.secrets_set?.includes(fd.k) ? "Already set. Leave blank to keep it." : "Stored encrypted. Never shown again."}>
            {input(fd, f.secrets[fd.k] ?? "", (v) => setSec(fd.k, v), "password")}
          </Field>
        ))}
      </div>
      {schema.note && <div className="hint">{schema.note}</div>}
      <div className="row">
        <button className="btn primary" disabled={busy}>{busy ? "Saving..." : "Save"}</button>
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}
