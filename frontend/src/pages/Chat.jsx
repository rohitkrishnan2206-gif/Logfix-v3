import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamPost } from "../api.js";
import { Markdown } from "../md.jsx";
import { ErrorBox, LlmBadge, Trace, useLoad } from "../ui.jsx";

const SUGGEST = [
  ["What is failing right now?", "Top error patterns in the last hour"],
  ["Why is payment-service failing?", "Root cause with log evidence"],
  ["Which runbook fits a connection pool error?", "Search the knowledge base"],
  ["Summarize open incidents", "What needs attention first"],
];
const TOOL_LABEL = { list_sources: "Checking log sources", search_logs: "Searching logs", get_log_context: "Reading surrounding logs", top_error_patterns: "Checking error patterns",
  error_timeline: "Building the timeline", get_incident: "Reading the incident", find_similar_incidents: "Finding similar incidents", search_runbooks: "Searching runbooks" };

export default function Chat() {
  const sessions = useLoad(() => api("/api/chat/sessions"));
  const [sid, setSid] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [live, setLive] = useState(null);       // { text, steps:[{name,status}] } while streaming
  const [err, setErr] = useState("");
  const [provider, setProvider] = useState(null);
  const [editing, setEditing] = useState(null);
  const endRef = useRef(null), taRef = useRef(null), abortRef = useRef(null), stickRef = useRef(true), scrollRef = useRef(null);
  const busy = !!live;

  useEffect(() => { if (stickRef.current) endRef.current?.scrollIntoView({ block: "end" }); }, [msgs, live]);
  useEffect(() => { const t = taRef.current; if (t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; } }, [text]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const onScroll = () => { const el = scrollRef.current; if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; };

  async function open(id) {
    if (busy) return;
    setSid(id); setErr(""); setProvider(null);
    try { setMsgs((await api(`/api/chat/sessions/${id}`)).messages); stickRef.current = true; } catch (e) { setErr(e.message); }
  }
  function newChat() { if (busy) return; setSid(null); setMsgs([]); setErr(""); setProvider(null); taRef.current?.focus(); }
  async function remove(id) {
    if (!window.confirm("Delete this chat?")) return;
    try { await api(`/api/chat/sessions/${id}`, { method: "DELETE" }); if (sid === id) newChat(); sessions.reload(true); } catch (e) { setErr(e.message); }
  }
  async function rename(id, title) {
    setEditing(null);
    if (!title.trim()) return;
    try { await api(`/api/chat/sessions/${id}`, { method: "PATCH", body: { title: title.trim() } }); sessions.reload(true); } catch (e) { setErr(e.message); }
  }

  const run = useCallback(async (id, body, optimisticUser) => {
    setErr("");
    if (optimisticUser) setMsgs((m) => [...m, { id: "tmp-" + Date.now(), role: "user", content: optimisticUser }]);
    setLive({ text: "", steps: [] });
    stickRef.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    let final = null;
    try {
      await streamPost(`/api/chat/sessions/${id}/stream`, body, (ev, d) => {
        if (ev === "delta") setLive((l) => l && { ...l, text: l.text + d.text });
        else if (ev === "discard_text") setLive((l) => l && { ...l, text: "" });
        else if (ev === "step") setLive((l) => {
          if (!l) return l;
          const steps = l.steps.slice();
          if (d.status === "running") steps.push({ name: d.name, status: "run" });
          else { const j = steps.findLastIndex((s) => s.name === d.name && s.status === "run"); if (j >= 0) steps[j] = { ...steps[j], status: d.status === "ok" ? "done" : "err" }; }
          return { ...l, steps };
        });
        else if (ev === "answer") { final = d; setProvider(d.provider); if (d.error) setErr(d.error); }
        else if (ev === "error") setErr(d.message);
      }, ac.signal);
    } catch (e) {
      setErr(e.message);
    } finally {
      abortRef.current = null;
      setLive(null);
      try { setMsgs((await api(`/api/chat/sessions/${id}`)).messages); } catch { if (final) setMsgs((m) => [...m, { id: "a-" + Date.now(), role: "assistant", content: final.text, trace: final.trace }]); }
      sessions.reload(true);
    }
  }, [sessions]);

  async function send(q) {
    const t = (q ?? text).trim();
    if (!t || busy) return;
    setText("");
    let id = sid;
    try { if (!id) { id = (await api("/api/chat/sessions", { method: "POST" })).id; setSid(id); } } catch (e) { setErr(e.message); return; }
    run(id, { text: t }, t);
  }
  const stop = () => abortRef.current?.abort();
  function regenerate() {
    if (busy || !sid) return;
    setMsgs((m) => (m.length && m[m.length - 1].role === "assistant" ? m.slice(0, -1) : m));
    run(sid, { regenerate: true });
  }
  const copy = (t) => navigator.clipboard?.writeText(t).catch(() => {});
  const lastAssistant = msgs.length && msgs[msgs.length - 1].role === "assistant";

  return (
    <>
      <div className="page-h"><h1>Chat</h1><span className="sub">ask about your logs, incidents and runbooks</span><span className="right"><LlmBadge llm={provider} /></span></div>
      <div className="gpt">
        <aside className="gpt-side" aria-label="Chats">
          <button className="btn newbtn" onClick={newChat} disabled={busy}>+ New chat</button>
          <div className="gpt-list">
            {(sessions.data || []).length === 0 && <div className="muted small" style={{ padding: 10 }}>No chats yet.</div>}
            {(sessions.data || []).map((s) => (
              <div className={`gpt-item ${s.id === sid ? "on" : ""}`} key={s.id}>
                {editing === s.id ? (
                  <input autoFocus defaultValue={s.title} maxLength={80} aria-label="Chat title" onBlur={(e) => rename(s.id, e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") setEditing(null); }} />
                ) : (
                  <button className="sess" aria-current={s.id === sid} onClick={() => open(s.id)} onDoubleClick={() => setEditing(s.id)} title={`${s.title} (double-click to rename)`}>{s.title}</button>
                )}
                <button className="x" aria-label={`Rename ${s.title}`} title="Rename" onClick={() => setEditing(s.id)}>&#9998;</button>
                <button className="x" aria-label={`Delete ${s.title}`} title="Delete" onClick={() => remove(s.id)}>&#10005;</button>
              </div>
            ))}
          </div>
        </aside>
        <div className="gpt-main">
          <div className="gpt-scroll" ref={scrollRef} onScroll={onScroll}>
            <div className="gpt-inner">
              <ErrorBox error={err} />
              {msgs.length === 0 && !busy && (
                <div className="gpt-empty">
                  <h2>How can I help with your cloud logs?</h2>
                  <div className="muted">I look at real collected logs with tools and cite log ids. I am read-only: I suggest steps, you run them.</div>
                  <div className="gpt-sugg">{SUGGEST.map(([q, d]) => <button key={q} onClick={() => send(q)}><b>{q}</b><div className="muted small">{d}</div></button>)}</div>
                </div>
              )}
              {msgs.map((m, i) => (
                <div key={m.id + "-" + i} className={`turn ${m.role}`}>
                  <div className="av" aria-hidden="true">{m.role === "user" ? "You" : "AI"}</div>
                  <div className="tbody">
                    <div className="who">{m.role === "user" ? "You" : "LogFix AI"}</div>
                    {m.role === "user" ? <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div> : <Markdown text={m.content} />}
                    {m.trace && <Trace trace={m.trace} label="How I got this" />}
                    {m.role === "assistant" && !busy && (
                      <div className="turn-actions">
                        <button className="btn sm" onClick={() => copy(m.content)}>Copy</button>
                        {i === msgs.length - 1 && <button className="btn sm" onClick={regenerate}>Regenerate</button>}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {live && (
                <div className="turn assistant">
                  <div className="av" aria-hidden="true">AI</div>
                  <div className="tbody" aria-live="polite">
                    <div className="who">LogFix AI</div>
                    {live.steps.length > 0 && <div className="toolchips">{live.steps.map((s, i) => <span key={i} className={`toolchip ${s.status}`}>{TOOL_LABEL[s.name] || s.name}</span>)}</div>}
                    {live.text ? <div className="cursor"><Markdown text={live.text} /></div> : live.steps.every((s) => s.status !== "run") && <span className="muted cursor">Thinking</span>}
                  </div>
                </div>
              )}
              <div ref={endRef} />
            </div>
          </div>
          <div className="gpt-compose">
            <div className="gpt-box">
              <textarea ref={taRef} aria-label="Message" rows={1} value={text} onChange={(e) => setText(e.target.value)} maxLength={4000}
                placeholder="Message LogFix AI" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
              {busy
                ? <button className="btn send" onClick={stop} aria-label="Stop generating" title="Stop">&#9632;</button>
                : <button className="btn primary send" disabled={!text.trim()} onClick={() => send()} aria-label="Send" title="Send (Enter)">&#10148;</button>}
            </div>
            <div className="gpt-foot">Enter to send, Shift+Enter for a new line. Answers can be wrong; check the cited logs.{lastAssistant ? "" : ""}</div>
          </div>
        </div>
      </div>
    </>
  );
}
