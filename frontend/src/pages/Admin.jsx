import { useEffect, useState } from "react";
import { api } from "../api.js";
import { can, Card, Empty, ErrorBox, Field, fmtTime, LlmBadge, Spinner, useLoad } from "../ui.jsx";

const TABS = [["users", "Users"], ["llm", "AI model"], ["detection", "Detection"], ["integrations", "Integrations"], ["audit", "Audit log"]];

export default function Admin({ user }) {
  const [tab, setTab] = useState("users");
  if (!can(user, "admin")) return <Empty>Admin access required.</Empty>;
  return (
    <>
      <div className="page-h"><h1>Administration</h1></div>
      <div className="tabs" role="tablist">
        {TABS.map(([id, l]) => <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{l}</button>)}
      </div>
      {tab === "users" && <Users me={user} />}
      {tab === "llm" && <Llm />}
      {tab === "detection" && <Detection />}
      {tab === "integrations" && <Integrations />}
      {tab === "audit" && <Audit />}
    </>
  );
}

function Users({ me }) {
  const { data, error, loading, reload } = useLoad(() => api("/api/users"));
  const [f, setF] = useState({ username: "", password: "", role: "viewer" });
  const [msg, setMsg] = useState({});
  const [err, setErr] = useState("");
  const guard = async (fn) => { setErr(""); try { await fn(); await reload(true); } catch (e) { setErr(e.message); } };
  const patch = (u, body) => guard(() => api(`/api/users/${u.id}`, { method: "PATCH", body }));
  const reset = (u) => { const pw = window.prompt(`New temporary password for ${u.username} (10+ chars, mixed case + digit). They must change it at next login.`); if (pw) patch(u, { password: pw }); };
  const create = (e) => { e.preventDefault(); guard(async () => { await api("/api/users", { method: "POST", body: f }); setF({ username: "", password: "", role: "viewer" }); setMsg({ ok: `Created. ${f.username} must change the password at first login.` }); }); };
  return (
    <div className="stack">
      <ErrorBox error={err || error} />
      <Card title="Users">
        {loading && !data ? <Spinner /> : (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>Username</th><th>Role</th><th>Status</th><th /></tr></thead>
            <tbody>{(data || []).map((u) => (
              <tr key={u.id}>
                <td>{u.username}{u.id === me.id && <span className="muted"> (you)</span>}</td>
                <td><select aria-label={`Role for ${u.username}`} value={u.role} onChange={(e) => patch(u, { role: e.target.value })}><option>viewer</option><option>analyst</option><option>admin</option></select></td>
                <td>{u.disabled ? <span className="badge bad">disabled</span> : <span className="badge ok">active</span>}{u.must_change && <span className="badge warn" style={{ marginLeft: 6 }}>must change password</span>}</td>
                <td className="row">
                  <button className="btn sm" onClick={() => patch(u, { disabled: !u.disabled })}>{u.disabled ? "Enable" : "Disable"}</button>
                  <button className="btn sm" onClick={() => reset(u)}>Reset password</button>
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Card title="Create user">
        {msg.ok && <div className="alert alert-ok">{msg.ok}</div>}
        <form className="form-grid" onSubmit={create}>
          <Field label="Username"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required minLength={3} pattern="[A-Za-z0-9_.@\-]+" /></Field>
          <Field label="Temporary password" hint="10+ characters with upper case, lower case and a digit."><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required autoComplete="new-password" /></Field>
          <Field label="Role"><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option>viewer</option><option>analyst</option><option>admin</option></select></Field>
          <div style={{ alignSelf: "end" }}><button className="btn primary">Create user</button></div>
        </form>
      </Card>
    </div>
  );
}

function Llm() {
  const { data, error, loading, reload } = useLoad(() => api("/api/settings/llm"));
  const [f, setF] = useState(null);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { if (data && !f) setF({ ...data, azure_api_key: "", access_key_id: "", secret_access_key: "" }); }, [data, f]);
  if (!f) return loading ? <Spinner /> : <ErrorBox error={error} onRetry={reload} />;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  async function save(e) {
    e.preventDefault(); setBusy("save"); setErr(""); setRes(null);
    const body = {};
    ["provider", "bedrock_model_id", "aws_region", "azure_openai_endpoint", "azure_openai_deployment", "azure_openai_api_version"].forEach((k) => { body[k] = f[k]; });
    ["azure_api_key", "access_key_id", "secret_access_key"].forEach((k) => { if (f[k]) body[k] = f[k]; });
    try { await api("/api/settings/llm", { method: "PUT", body }); setF(null); await reload(true); setRes({ ok: "Saved." }); } catch (e2) { setErr(e2.message); } finally { setBusy(""); }
  }
  async function test() {
    setBusy("test"); setRes(null);
    try { const r = await api("/api/settings/llm/test", { method: "POST" }); setRes(r.ok ? { ok: `Model replied: "${r.reply}" (${r.provider} / ${r.model})` } : { error: r.error }); } catch (e2) { setRes({ error: e2.message }); } finally { setBusy(""); }
  }
  return (
    <Card title="AI model" actions={<LlmBadge llm={data.active} />}>
      <ErrorBox error={err} />
      {res && <div className={`alert ${res.error ? "alert-err" : "alert-ok"}`} role="status">{res.error || res.ok}</div>}
      <form className="stack" onSubmit={save}>
        <div className="form-grid">
          <Field label="Provider" hint="mock = fixed rules, no AI. Use bedrock or azure_openai for real reasoning.">
            <select value={f.provider} onChange={set("provider")}><option value="mock">mock (heuristic, no LLM)</option><option value="bedrock">AWS Bedrock</option><option value="azure_openai">Azure OpenAI</option></select>
          </Field>
        </div>
        {f.provider === "bedrock" && (
          <div className="form-grid">
            <Field label="Model ID or inference profile"><input value={f.bedrock_model_id} onChange={set("bedrock_model_id")} /></Field>
            <Field label="Region"><input value={f.aws_region} onChange={set("aws_region")} /></Field>
            <Field label="Access key ID (write-only)" hint={data.has_aws_keys ? "Keys already set. Leave blank to keep." : "Leave blank to use the server's IAM role (recommended)."}><input type="password" value={f.access_key_id} onChange={set("access_key_id")} autoComplete="off" /></Field>
            <Field label="Secret access key (write-only)"><input type="password" value={f.secret_access_key} onChange={set("secret_access_key")} autoComplete="off" /></Field>
          </div>
        )}
        {f.provider === "azure_openai" && (
          <div className="form-grid">
            <Field label="Endpoint"><input value={f.azure_openai_endpoint} onChange={set("azure_openai_endpoint")} placeholder="https://myres.openai.azure.com" /></Field>
            <Field label="Deployment name"><input value={f.azure_openai_deployment} onChange={set("azure_openai_deployment")} /></Field>
            <Field label="API version"><input value={f.azure_openai_api_version} onChange={set("azure_openai_api_version")} /></Field>
            <Field label="API key (write-only)" hint={data.has_azure_api_key ? "Key already set. Leave blank to keep." : "Leave blank to use Managed Identity."}><input type="password" value={f.azure_api_key} onChange={set("azure_api_key")} autoComplete="off" /></Field>
          </div>
        )}
        <div className="row">
          <button className="btn primary" disabled={!!busy}>{busy === "save" ? "Saving..." : "Save"}</button>
          <button type="button" className="btn" disabled={!!busy} onClick={test}>{busy === "test" ? "Testing..." : "Test connection"}</button>
        </div>
      </form>
    </Card>
  );
}

function Detection() {
  const { data, error, loading, reload } = useLoad(() => api("/api/settings/detection"));
  const [f, setF] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => { if (data && !f) setF(data); }, [data, f]);
  if (!f) return loading ? <Spinner /> : <ErrorBox error={error} onRetry={reload} />;
  const num = (k) => (e) => setF({ ...f, [k]: Number(e.target.value) });
  const thr = (k) => (e) => setF({ ...f, thresholds: { ...f.thresholds, [k]: Number(e.target.value) } });
  async function save(e) {
    e.preventDefault(); setMsg(null);
    try { setF(await api("/api/settings/detection", { method: "PUT", body: f })); setMsg({ ok: "Saved." }); } catch (e2) { setMsg({ error: e2.message }); }
  }
  return (
    <Card title="Incident detection">
      {msg && <div className={`alert ${msg.error ? "alert-err" : "alert-ok"}`} role="status">{msg.error || msg.ok}</div>}
      <form className="stack" onSubmit={save}>
        <div className="form-grid">
          <Field label="Lookback window (minutes)"><input type="number" min="5" max="1440" value={f.lookback_minutes} onChange={num("lookback_minutes")} /></Field>
          <div />
          {["CRITICAL", "HIGH", "MEDIUM"].map((s) => (
            <Field key={s} label={`Open an incident after N ${s} events`}><input type="number" min="1" value={f.thresholds[s]} onChange={thr(s)} /></Field>
          ))}
          <label className="check"><input type="checkbox" checked={f.auto_pipeline !== false} onChange={(e) => setF({ ...f, auto_pipeline: e.target.checked })} /> Run the agent pipeline automatically for every new incident (classify, root cause, remediation steps, alerts, guide, ticket)</label>
          <Field label="Automatic runs per hour (cap)"><input type="number" min="0" max="100" value={f.auto_per_hour} onChange={num("auto_per_hour")} /></Field>
          <label className="check"><input type="checkbox" checked={f.auto_investigate} onChange={(e) => setF({ ...f, auto_investigate: e.target.checked })} /> Auto-investigate new HIGH and CRITICAL incidents (uses the AI model, costs tokens)</label>
          <Field label="Legacy Slack / Teams webhook (use Integrations instead)" hint="Kept for older setups."><input value={f.webhook_url} onChange={(e) => setF({ ...f, webhook_url: e.target.value })} placeholder="https://hooks.slack.com/services/..." autoComplete="off" /></Field>
        </div>
        <div><button className="btn primary">Save</button></div>
      </form>
    </Card>
  );
}

function Audit() {
  const { data, error, loading, reload } = useLoad(() => api("/api/audit?limit=300"));
  return (
    <Card title="Audit log" actions={<button className="btn sm" onClick={() => reload()}>Refresh</button>}>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : !data || data.length === 0 ? <Empty>No entries.</Empty> : (
        <div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
          <tbody>{data.map((a) => (
            <tr key={a.id}><td className="nowrap">{fmtTime(a.ts)}</td><td>{a.actor}</td><td><code>{a.action}</code></td><td>{a.target}</td><td className="mono muted">{a.detail}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </Card>
  );
}

const PRIOS = ["P1", "P2", "P3", "P4"];

function Integrations() {
  const { data, error, loading, reload } = useLoad(() => api("/api/integrations"));
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <div className="alert alert-warn">Alerts are sent by the Notifier agent only for incidents at or above each channel's priority. Secrets are stored encrypted and never shown again. Use &quot;Send test&quot; to check each one.</div>
      <IntCard name="slack" title="Slack / Microsoft Teams" data={data.slack} reload={reload} fields={[["url", "Incoming webhook URL", "password", "has_url", "https://hooks.slack.com/services/..."]]} />
      <IntCard name="pagerduty" title="PagerDuty (Events API v2)" data={data.pagerduty} reload={reload} fields={[["routing_key", "Integration / routing key", "password", "has_routing_key", ""]]} />
      <IntCard name="email" title="Email (SMTP)" data={data.email} reload={reload} fields={[["host", "SMTP host", "text"], ["port", "Port", "number"], ["username", "Username", "text"], ["password", "Password", "password", "has_password"], ["from_addr", "From address", "text"], ["to", "To (comma separated)", "text"]]} />
      <IntCard name="servicenow" title="ServiceNow (ITSM tickets)" data={data.servicenow} reload={reload} fields={[["instance_url", "Instance URL", "text", null, "https://dev12345.service-now.com"], ["username", "Integration user", "text"], ["password", "Password", "password", "has_password"]]} />
    </div>
  );
}

function IntCard({ name, title, data, fields, reload }) {
  const [f, setF] = useState(() => {
    const o = { enabled: !!data.enabled, min_priority: data.min_priority };
    fields.forEach(([k, , t]) => { o[k] = t === "password" ? "" : Array.isArray(data[k]) ? data[k].join(", ") : (data[k] ?? ""); });
    return o;
  });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  async function save(e) {
    e && e.preventDefault(); setMsg(null); setBusy(true);
    const body = { enabled: f.enabled, min_priority: f.min_priority };
    fields.forEach(([k, , t]) => { if (t === "password") { if (f[k]) body[k] = f[k]; } else body[k] = t === "number" ? Number(f[k]) : f[k]; });
    try { await api(`/api/integrations/${name}`, { method: "PUT", body }); setMsg({ ok: "Saved." }); setF((x) => { const y = { ...x }; fields.forEach(([k, , t]) => { if (t === "password") y[k] = ""; }); return y; }); reload(true); }
    catch (e2) { setMsg({ error: e2.message }); } finally { setBusy(false); }
  }
  async function test() {
    setMsg(null); setBusy(true);
    try { const r = await api(`/api/integrations/${name}/test`, { method: "POST" }); setMsg(r.ok ? { ok: `Test passed: ${r.detail}` } : { error: `Test failed: ${r.error}` }); }
    catch (e2) { setMsg({ error: e2.message }); } finally { setBusy(false); }
  }
  return (
    <Card title={title} actions={<span className={`badge ${data.enabled ? "ok" : ""}`}>{data.enabled ? "enabled" : "off"}</span>}>
      {msg && <div className={`alert ${msg.error ? "alert-err" : "alert-ok"}`} role="status">{msg.error || msg.ok}</div>}
      <form className="form-grid" onSubmit={save}>
        {fields.map(([k, label, type, has, ph]) => (
          <Field key={k} label={label} hint={has && data[has] ? "A value is saved. Leave blank to keep it." : ""}>
            <input type={type} autoComplete="off" value={f[k]} placeholder={ph || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
          </Field>
        ))}
        <Field label="Send for priority at or above">
          <select value={f.min_priority} onChange={(e) => setF({ ...f, min_priority: e.target.value })}>{PRIOS.map((p) => <option key={p}>{p}</option>)}</select>
        </Field>
        <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Enabled</label>
        <div className="row"><button className="btn primary" disabled={busy}>Save</button><button type="button" className="btn" disabled={busy} onClick={test}>Send test</button></div>
      </form>
    </Card>
  );
}
