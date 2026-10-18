import { useEffect, useState } from "react";
import { api } from "../api.js";
import { can, Card, Diff, Empty, ErrorBox, Field, fmtTime, LlmBadge, relTime, Spinner, Trace, useInterval, useLoad } from "../ui.jsx";

export default function Code({ user }) {
  const repos = useLoad(() => api("/api/repos"));
  const [rid, setRid] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState(null);
  const [filter, setFilter] = useState("pending");
  const props = useLoad(() => api("/api/proposals" + (filter ? `?status=${filter}` : "")), [filter]);
  const [nr, setNr] = useState({ name: "", root_path: "" });
  const admin = can(user, "admin"), analyst = can(user, "analyst");
  const current = rid || (repos.data && repos.data[0] ? String(repos.data[0].id) : "");
  const cur = (repos.data || []).find((r) => String(r.id) === current);
  const syncing = (repos.data || []).some((r) => r.sync_status === "syncing" || r.sync_status === "pending");
  useInterval(() => repos.reload(true), 2500, syncing);

  async function ask(e) {
    e.preventDefault();
    setBusy(true); setErr(""); setOut(null);
    try { setOut(await api(`/api/repos/${current}/ask`, { method: "POST", body: { question: q } })); props.reload(true); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  async function addRepo(e) {
    e.preventDefault(); setErr("");
    try { await api("/api/repos", { method: "POST", body: nr }); setNr({ name: "", root_path: "" }); repos.reload(true); } catch (e2) { setErr(e2.message); }
  }
  async function delRepo() {
    if (!window.confirm("Remove this repository from LogFix? Files on disk are not touched.")) return;
    try { await api(`/api/repos/${current}`, { method: "DELETE" }); setRid(""); repos.reload(true); } catch (e2) { setErr(e2.message); }
  }
  const repoOf = (p) => (repos.data || []).find((r) => r.id === p.repo_id);
  async function openPr(p) {
    if (!window.confirm(`Create a branch and open a pull request on GitHub for ${p.path}?`)) return;
    setErr("");
    try { const r = await api(`/api/proposals/${p.id}/pr`, { method: "POST" }); window.alert(`Pull request created${r.url ? `: ${r.url}` : ` on branch ${r.branch}`}`); } catch (e2) { setErr(e2.message); }
    props.reload(true);
  }
  async function decide(p, action) {
    if (action === "apply" && !window.confirm(`Apply this change to ${p.path}? It will edit the file on the server.`)) return;
    setErr("");
    try { await api(`/api/proposals/${p.id}/${action}`, { method: "POST" }); } catch (e2) { setErr(e2.message); }
    props.reload(true);
  }

  return (
    <>
      <div className="page-h"><h1>Code assistant</h1><span className="sub">ask about a repository; fixes are proposed, never applied automatically</span></div>
      <ErrorBox error={err || repos.error} />
      <div className="alert alert-warn">The assistant can read files and propose edits. It cannot run code or tests. An admin must review the diff and apply it.</div>
      {cur && cur.kind === "github" && <GithubPanel repo={cur} admin={admin} analyst={analyst} reload={() => repos.reload(true)} setErr={setErr} />}
      <div style={{ height: 14 }} />
      <Card title="Ask about a repository">
        {repos.loading && !repos.data ? <Spinner /> : (repos.data || []).length === 0 ? <Empty>No repositories registered. {admin ? "Add one below." : "Ask an admin to add one."}</Empty> : (
          <form className="stack" onSubmit={ask}>
            <div className="row">
              <Field label="Repository">
                <select value={current} onChange={(e) => setRid(e.target.value)}>{repos.data.map((r) => <option key={r.id} value={r.id}>{r.name}{r.kind === "github" ? " (GitHub)" : ""}</option>)}</select>
              </Field>
              {admin && <button type="button" className="btn sm danger" onClick={delRepo}>Remove repository</button>}
            </div>
            <Field label="Question or change request" hint="Example: Why can the connection pool run out in payment_service/db.py? Propose a fix.">
              <textarea rows={3} value={q} onChange={(e) => setQ(e.target.value)} required minLength={3} maxLength={4000} disabled={!analyst} />
            </Field>
            <div><button className="btn primary" disabled={!analyst || busy || !q.trim() || (cur && cur.kind === "github" && cur.sync_status !== "ready")} title={analyst ? "" : "Requires analyst role"}>{busy ? "Working..." : "Ask"}</button></div>
          </form>
        )}
        {busy && <Spinner label="The agent is reading the repository" />}
        {out && (
          <div style={{ marginTop: 14 }}>
            <div className="row"><LlmBadge llm={out.provider} />{out.error && <span className="badge bad">failed</span>}</div>
            {out.error ? <div className="alert alert-err" role="alert">{out.error}</div> : <p className="answer">{out.answer}</p>}
            {out.proposals && out.proposals.length > 0 && <div className="alert alert-ok">{out.proposals.length} change(s) proposed. Review them below.</div>}
            <Trace trace={out.trace} open />
          </div>
        )}
      </Card>
      <div style={{ height: 14 }} />
      <Card title="Change proposals" actions={
        <select aria-label="Proposal status" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="pending">Pending</option><option value="applied">Applied</option><option value="rejected">Rejected</option><option value="stale">Stale</option><option value="">All</option>
        </select>}>
        {props.loading && !props.data ? <Spinner /> : !props.data || props.data.length === 0 ? <Empty>No {filter || ""} proposals.</Empty> : (
          <div className="stack">{props.data.map((p) => (
            <div key={p.id} className="card" style={{ background: "var(--panel2)" }}>
              <div className="row">
                <strong className="mono">{p.path}</strong>
                <span className={`badge ${p.status === "applied" ? "ok" : p.status === "pending" ? "warn" : "bad"}`}>{p.status}</span>
                <span className="muted">#{p.id} by {p.created_by || "?"}, {fmtTime(p.created_at)}</span>
              </div>
              {p.rationale && <p style={{ margin: "6px 0" }}>{p.rationale}</p>}
              <Diff diff={p.diff} />
              {p.status === "pending" && (
                <div className="row">
                  {!(repoOf(p) && repoOf(p).kind === "github") && <button className="btn primary" disabled={!admin} title={admin ? "" : "Only an admin can apply changes"} onClick={() => decide(p, "apply")}>Apply</button>}
                  {repoOf(p) && repoOf(p).kind === "github" && (
                    <button className="btn" disabled={!admin || !repoOf(p).allow_pr} title={repoOf(p).allow_pr ? "Creates a branch and a pull request on GitHub" : "Enable pull requests for this repository first"} onClick={() => openPr(p)}>Open pull request</button>)}
                  <button className="btn" disabled={!analyst} onClick={() => decide(p, "reject")}>Reject</button>
                </div>
              )}
            </div>
          ))}</div>
        )}
      </Card>
      {admin && <LinkGithub onLinked={() => repos.reload(true)} setErr={setErr} />}
      {admin && (
        <>
          <div style={{ height: 14 }} />
          <Card title="Register a server directory">
            <form className="form-grid" onSubmit={addRepo}>
              <Field label="Name"><input value={nr.name} onChange={(e) => setNr({ ...nr, name: e.target.value })} required /></Field>
              <Field label="Directory on the server" hint="Sensitive files (.env, keys, credentials) are never readable. If LOGFIX_REPO_ROOTS is set the path must be inside it.">
                <input value={nr.root_path} onChange={(e) => setNr({ ...nr, root_path: e.target.value })} required placeholder="/home/ubuntu/my-service" />
              </Field>
              <div><button className="btn primary">Add repository</button></div>
            </form>
          </Card>
        </>
      )}
    </>
  );
}

function Copy({ value }) {
  const [ok, setOk] = useState(false);
  return <button type="button" className="btn sm" onClick={() => { navigator.clipboard?.writeText(value).then(() => { setOk(true); setTimeout(() => setOk(false), 1200); }).catch(() => {}); }}>{ok ? "Copied" : "Copy"}</button>;
}

function LinkGithub({ onLinked, setErr }) {
  const [f, setF] = useState({ url: "", branch: "", token: "", allow_pr: false, pr_comments: false });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  async function submit(e) {
    e.preventDefault(); setErr(""); setBusy(true);
    const body = { url: f.url.trim(), allow_pr: f.allow_pr, pr_comments: f.pr_comments };
    if (f.branch.trim()) body.branch = f.branch.trim();
    if (f.token.trim()) body.token = f.token.trim();
    try { setDone(await api("/api/repos/github", { method: "POST", body })); setF({ url: "", branch: "", token: "", allow_pr: false, pr_comments: false }); onLinked(); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <>
      <div style={{ height: 14 }} />
      <Card title="Link a GitHub repository">
        {done && (
          <div className="alert alert-ok" role="status">
            <div style={{ width: "100%" }}>
              <b>{done.name} linked.</b> It is cloning now. To make GitHub tell LogFix about pushes and pull requests, add a webhook (Repo &rsaquo; Settings &rsaquo; Webhooks &rsaquo; Add webhook):
              <div className="copyline" style={{ marginTop: 6 }}><span className="muted">Payload URL</span><code>{done.webhook_url}</code><Copy value={done.webhook_url} /></div>
              <div className="copyline" style={{ marginTop: 6 }}><span className="muted">Secret</span><code>{done.webhook_secret}</code><Copy value={done.webhook_secret} /></div>
              <div className="small" style={{ marginTop: 6 }}>Content type: <b>application/json</b>. Events: <b>Pushes</b> and <b>Pull requests</b>. The secret is shown only now; you can rotate it later. GitHub must be able to reach this URL (it must be public https).</div>
              <button type="button" className="btn sm" style={{ marginTop: 6 }} onClick={() => setDone(null)}>I saved it</button>
            </div>
          </div>
        )}
        <form className="form-grid" onSubmit={submit}>
          <Field label="Repository URL" hint="https://github.com/owner/repo. GitHub Enterprise hosts must be allowed with LOGFIX_GIT_HOSTS."><input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} required placeholder="https://github.com/acme/payment-service" /></Field>
          <Field label="Branch (optional)" hint="Leave empty for the default branch."><input value={f.branch} onChange={(e) => setF({ ...f, branch: e.target.value })} placeholder="main" /></Field>
          <Field label="Access token (needed for private repos)" hint="Fine-grained token. Read-only: Contents (read). To open PRs also Contents + Pull requests (write). Stored encrypted, never shown again."><input type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} /></Field>
          <div className="stack">
            <label className="check"><input type="checkbox" checked={f.allow_pr} onChange={(e) => setF({ ...f, allow_pr: e.target.checked })} /> Allow opening pull requests from approved proposals</label>
            <label className="check"><input type="checkbox" checked={f.pr_comments} onChange={(e) => setF({ ...f, pr_comments: e.target.checked })} /> Post automated review comments on pull requests</label>
          </div>
          <div><button className="btn primary" disabled={busy}>{busy ? "Linking..." : "Link repository"}</button></div>
        </form>
      </Card>
    </>
  );
}

function GithubPanel({ repo, admin, analyst, reload, setErr }) {
  const reviews = useLoad(() => api(`/api/repos/${repo.id}/reviews`), [repo.id]);
  const [secret, setSecret] = useState(null);
  const [prNum, setPrNum] = useState("");
  const [open, setOpen] = useState(null);
  useInterval(() => reviews.reload(true), 3000, (reviews.data || []).some((r) => r.status === "running"));
  useEffect(() => { setSecret(null); }, [repo.id]);
  const badge = { ready: "ok", syncing: "warn", pending: "warn", error: "bad" }[repo.sync_status] || "";
  const call = async (fn) => { setErr(""); try { await fn(); } catch (e) { setErr(e.message); } };
  return (
    <Card title={`GitHub: ${repo.name}`} actions={<>
      <button className="btn sm" disabled={!analyst || repo.sync_status === "syncing"} onClick={() => call(async () => { await api(`/api/repos/${repo.id}/sync`, { method: "POST" }); reload(); })}>Sync now</button>
    </>}>
      <dl className="kv">
        <dt>Status</dt><dd><span className={`badge ${badge}`}>{repo.sync_status}</span> {repo.last_synced_at && <span className="muted">last synced {relTime(repo.last_synced_at)}, commit <code>{(repo.last_commit || "").slice(0, 8)}</code>, branch {repo.branch}</span>}</dd>
        {repo.sync_error && <><dt>Error</dt><dd className="bad-t">{repo.sync_error}</dd></>}
        <dt>Repository</dt><dd>{repo.url} {repo.has_token ? <span className="badge ok">token saved</span> : <span className="badge">public / no token</span>}</dd>
        <dt>Webhook</dt><dd><div className="copyline"><code>{repo.webhook_url}</code><Copy value={repo.webhook_url} /></div></dd>
      </dl>
      {admin && (
        <div className="stack">
          <div className="row">
            <label className="check"><input type="checkbox" checked={!!repo.allow_pr} onChange={(e) => call(async () => { await api(`/api/repos/${repo.id}`, { method: "PATCH", body: { allow_pr: e.target.checked } }); reload(); })} /> Allow opening pull requests</label>
            <label className="check"><input type="checkbox" checked={!!repo.pr_comments} onChange={(e) => call(async () => { await api(`/api/repos/${repo.id}`, { method: "PATCH", body: { pr_comments: e.target.checked } }); reload(); })} /> Post review comments on PRs</label>
            <button className="btn sm" onClick={() => call(async () => { if (window.confirm("Generate a new webhook secret? The old one stops working immediately.")) setSecret(await api(`/api/repos/${repo.id}/webhook/rotate`, { method: "POST" })); })}>Rotate webhook secret</button>
            <button className="btn sm" onClick={() => { const t = window.prompt("New access token (stored encrypted):"); if (t) call(async () => { await api(`/api/repos/${repo.id}`, { method: "PATCH", body: { token: t } }); reload(); }); }}>Replace token</button>
          </div>
          {secret && <div className="alert alert-ok"><div style={{ width: "100%" }}>New secret (shown once): <div className="copyline"><code>{secret.webhook_secret}</code><Copy value={secret.webhook_secret} /></div></div></div>}
        </div>
      )}
      <h3>Pull request reviews</h3>
      {analyst && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); call(async () => { await api(`/api/repos/${repo.id}/reviews`, { method: "POST", body: { number: Number(prNum) } }); setPrNum(""); reviews.reload(true); }); }}>
          <input type="number" min="1" style={{ width: 150 }} placeholder="PR number" aria-label="Pull request number" value={prNum} onChange={(e) => setPrNum(e.target.value)} />
          <button className="btn sm" disabled={!prNum}>Review this PR</button>
          <span className="muted small">Reviews also run automatically when GitHub sends a pull request event.</span>
        </form>
      )}
      {reviews.loading && !reviews.data ? <Spinner /> : !reviews.data || reviews.data.length === 0 ? <Empty>No reviews yet.</Empty> : (
        <div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>PR</th><th>Status</th><th>Verdict</th><th>Risk</th><th>Findings</th><th>When</th></tr></thead>
          <tbody>{reviews.data.map((r) => (
            <tr key={r.id} onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: "pointer" }}>
              <td>#{r.pr_number} <span className="muted">{r.title}</span>
                {open === r.id && r.result && (
                  <div style={{ marginTop: 8, cursor: "default" }} onClick={(e) => e.stopPropagation()}>
                    <p>{r.result.summary}</p>
                    <div className="hint">Mode: {r.result.mode === "rules" ? "pattern rules only (no AI model configured)" : "AI model + pattern rules"}</div>
                    {r.result.findings.map((f, i) => <div key={i} className={`find ${f.severity}`}><b>{f.severity}</b> <code>{f.path}{f.line ? `:${f.line}` : ""}</code> {f.comment}</div>)}
                    {r.comment_url && <a href={r.comment_url} target="_blank" rel="noopener noreferrer">Comment on GitHub</a>}
                  </div>)}
                {r.error && <div className="bad-t small">{r.error}</div>}
              </td>
              <td><span className={`badge ${r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "warn"}`}>{r.status}</span></td>
              <td>{r.result ? r.result.verdict.replace("_", " ") : "-"}</td>
              <td>{r.result ? <span className={`badge ${r.result.risk === "high" ? "bad" : r.result.risk === "medium" ? "warn" : "ok"}`}>{r.result.risk}</span> : "-"}</td>
              <td>{r.result ? r.result.findings.length : "-"}</td>
              <td className="muted nowrap">{relTime(r.created_at)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Card>
  );
}
