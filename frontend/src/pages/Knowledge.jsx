import { useState } from "react";
import { api } from "../api.js";
import { useEffect } from "react";
import { can, Card, Empty, ErrorBox, Field, fmtTime, Spinner, takeHandoff, useLoad } from "../ui.jsx";

export default function Knowledge({ user }) {
  const docs = useLoad(() => api("/api/kb/docs"));
  const [q, setQ] = useState(() => takeHandoff("kbQ"));
  const [hits, setHits] = useState(null);
  const [doc, setDoc] = useState(null);
  const [form, setForm] = useState({ title: "", content: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const analyst = can(user, "analyst");
  useEffect(() => { if (q.trim()) api("/api/kb/search?q=" + encodeURIComponent(q)).then(setHits).catch((e) => setErr(e.message)); /* eslint-disable-next-line */ }, []);

  const guard = async (fn) => { setErr(""); setBusy(true); try { await fn(); } catch (e) { setErr(e.message); } finally { setBusy(false); } };
  const search = (e) => { e.preventDefault(); if (q.trim()) guard(async () => setHits(await api("/api/kb/search?q=" + encodeURIComponent(q)))); };
  const view = (id) => guard(async () => setDoc(await api(`/api/kb/docs/${id}`)));
  const add = (e) => { e.preventDefault(); guard(async () => { await api("/api/kb/docs", { method: "POST", body: form }); setForm({ title: "", content: "" }); docs.reload(true); }); };
  const del = (d) => { if (window.confirm(`Delete "${d.title}"?`)) guard(async () => { await api(`/api/kb/docs/${d.id}`, { method: "DELETE" }); if (doc?.id === d.id) setDoc(null); docs.reload(true); }); };

  return (
    <>
      <div className="page-h"><h1>Knowledge base</h1><span className="sub">runbooks and documentation the agents can search</span></div>
      <ErrorBox error={err || docs.error} />
      <Card title="Search">
        <form className="row" onSubmit={search}>
          <input className="grow" aria-label="Search the knowledge base" value={q} onChange={(e) => setQ(e.target.value)} placeholder="connection pool exhausted" />
          <button className="btn primary" disabled={busy}>Search</button>
        </form>
        <div className="hint" style={{ marginTop: 6 }}>BM25 keyword search, not embeddings. Wording matters: use words that appear in the runbook.</div>
        {hits && (hits.results.length === 0 ? <Empty>No matching runbooks.</Empty> : (
          <div className="stack" style={{ marginTop: 12 }}>{hits.results.map((h) => (
            <div key={h.doc_id}>
              <div className="row"><strong>{h.title}</strong><span className="chip">score {h.score}</span></div>
              <pre className="tprev" style={{ maxHeight: 160 }}>{h.snippet}</pre>
            </div>
          ))}</div>
        ))}
      </Card>
      <div style={{ height: 14 }} />
      <div className="grid g2">
        <Card title="Documents">
          {docs.loading && !docs.data ? <Spinner /> : !docs.data || docs.data.length === 0 ? <Empty>No documents.</Empty> : (
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>Title</th><th>Source</th><th className="num">Chunks</th><th /></tr></thead>
              <tbody>{docs.data.map((d) => (
                <tr key={d.id}>
                  <td><button className="btn link" onClick={() => view(d.id)}>{d.title}</button></td>
                  <td className="muted">{d.source}</td><td className="num">{d.chunks}</td>
                  <td><button className="btn sm danger" disabled={!analyst} onClick={() => del(d)}>Delete</button></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          {doc && (<><h3 style={{ marginTop: 14 }}>{doc.title}</h3><div className="hint">added {fmtTime(doc.created_at)}</div><div className="doc-body">{doc.content}</div></>)}
        </Card>
        <Card title="Add a runbook or document">
          <form className="stack" onSubmit={add}>
            <Field label="Title"><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required maxLength={200} disabled={!analyst} /></Field>
            <Field label="Content (Markdown; use ## headings per section)" hint="Secrets such as keys and passwords are redacted before storage.">
              <textarea rows={10} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} required minLength={10} disabled={!analyst} />
            </Field>
            <div><button className="btn primary" disabled={!analyst || busy} title={analyst ? "" : "Requires analyst role"}>Add document</button></div>
          </form>
        </Card>
      </div>
    </>
  );
}
