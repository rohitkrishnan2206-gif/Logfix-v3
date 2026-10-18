import { useState } from "react";
import { api, setToken } from "../api.js";
import { Field } from "../ui.jsx";

export function Login({ onLogin }) {
  const [username, setU] = useState("admin");
  const [password, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { username, password } });
      setToken(r.token);
      onLogin(r.user);
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth">
      <form className="card stack" onSubmit={submit}>
        <div>
          <h1>LogFix AI</h1>
          <div className="muted">Sign in to the investigation console</div>
        </div>
        {err && <div className="alert alert-err" role="alert">{err}</div>}
        <Field label="Username">
          <input value={username} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus required />
        </Field>
        <Field label="Password" hint="First run: the generated password is in data/initial_admin_password.txt on the server.">
          <input type="password" value={password} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required />
        </Field>
        <button className="btn primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </div>
  );
}

export function ChangePassword({ forced, onDone, onCancel, onLogout }) {
  const [old_password, setOld] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    if (n1 !== n2) return setErr("New passwords do not match");
    setBusy(true);
    try {
      await api("/api/auth/change-password", { method: "POST", body: { old_password, new_password: n1 } });
      await onDone();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth">
      <form className="card stack" onSubmit={submit}>
        <div>
          <h1>{forced ? "Set a new password" : "Change password"}</h1>
          {forced && <div className="muted">You must change the generated password before continuing.</div>}
        </div>
        {err && <div className="alert alert-err" role="alert">{err}</div>}
        <Field label="Current password">
          <input type="password" value={old_password} onChange={(e) => setOld(e.target.value)} autoComplete="current-password" autoFocus required />
        </Field>
        <Field label="New password" hint="At least 10 characters with upper case, lower case and a digit.">
          <input type="password" value={n1} onChange={(e) => setN1(e.target.value)} autoComplete="new-password" required />
        </Field>
        <Field label="Repeat new password">
          <input type="password" value={n2} onChange={(e) => setN2(e.target.value)} autoComplete="new-password" required />
        </Field>
        <div className="row">
          <button className="btn primary" disabled={busy}>{busy ? "Saving..." : "Save password"}</button>
          {!forced && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
          <button type="button" className="btn right" onClick={onLogout}>Sign out</button>
        </div>
      </form>
    </div>
  );
}
