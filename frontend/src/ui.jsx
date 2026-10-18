// Shared UI helpers and small components. All text is rendered as React text (never raw HTML).
import { useCallback, useEffect, useRef, useState } from "react";

export const ROLE_RANK = { viewer: 1, analyst: 2, admin: 3 };
export const can = (user, role) => !!user && (ROLE_RANK[user.role] || 0) >= ROLE_RANK[role];

// ---- hash router: #/page/id ----
function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, id] = h.split("/");
  return { page: page || "overview", id: id ? Number(id) : null };
}
export function useRoute() {
  const [r, setR] = useState(parseHash);
  useEffect(() => {
    const f = () => setR(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return r;
}
// One-shot values passed between the global search and pages.
export const handoff = { logsQ: "", kbQ: "" };
export const takeHandoff = (k) => { const v = handoff[k]; handoff[k] = ""; return v; };
export const nav = (page, id) => {
  window.location.hash = `#/${page}${id ? `/${id}` : ""}`;
};

// ---- data loading ----
export function useLoad(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const reload = useCallback(async (silent) => {
    const mine = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const d = await fnRef.current();
      if (mine === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e.message || String(e));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, deps);
  return { data, error, loading, reload, setData };
}

export function useInterval(fn, ms, enabled = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled || !ms) return undefined;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, enabled]);
}

// ---- formatting ----
export function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export function fmtClock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
export function relTime(iso) {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (isNaN(s)) return "-";
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ---- small components ----
export function SevBadge({ sev }) {
  return <span className={`badge sev-${(sev || "INFO").toLowerCase()}`}>{sev}</span>;
}
export function StatusBadge({ status }) {
  return <span className={`badge st-${status}`}>{status}</span>;
}
export function CloudBadge({ cloud }) {
  return <span className={`badge cloud-${cloud}`}>{cloud}</span>;
}
export function Spinner({ label = "Loading" }) {
  return (
    <div className="spinner" role="status">
      <span className="spin" aria-hidden="true" /> {label}...
    </div>
  );
}
export function ErrorBox({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="alert alert-err" role="alert">
      <span>{error}</span>
      {onRetry && (
        <button className="btn sm" onClick={() => onRetry()}>
          Retry
        </button>
      )}
    </div>
  );
}
export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}
export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-h">
          <h2>{title}</h2>
          <div className="row">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}
export function Field({ label, hint, children, full }) {
  return (
    <label className={`field ${full ? "full" : ""}`}>
      <span className="field-l">{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function LlmBadge({ llm }) {
  if (!llm) return null;
  if (llm.is_real === false)
    return (
      <span className="badge llm-mock" title="No AI model is configured. Answers come from fixed rules. Set up Bedrock or Azure OpenAI in Admin.">
        heuristic (no LLM)
      </span>
    );
  return (
    <span className="badge llm-real" title="Active AI model">
      {llm.provider} / {llm.model}
    </span>
  );
}

// ---- agent execution trace ----
function short(v) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s && s.length > 160 ? s.slice(0, 160) + "..." : s;
}
export function Trace({ trace, open = false, label = "Execution trace" }) {
  if (!trace || !trace.length) return null;
  const tools = trace.filter((t) => t.type === "tool").length;
  const ms = trace.reduce((a, t) => a + (t.ms || 0), 0);
  return (
    <details className="trace" open={open}>
      <summary>
        {label}: {trace.length} steps, {tools} tool calls, {ms} ms
      </summary>
      <ol className="trace-list">
        {trace.map((t, i) => (
          <li key={i} className={`tstep t-${t.type} ${t.ok === false || t.type === "error" ? "t-bad" : ""}`}>
            {t.type === "llm" && (
              <>
                <div className="tline">
                  <span className="badge t-llm">model</span>
                  <span className="muted">
                    {t.ms} ms
                    {t.usage && (t.usage.input || t.usage.output) ? `, tokens in ${t.usage.input} / out ${t.usage.output}` : ""}
                  </span>
                  <span>{t.tool_calls && t.tool_calls.length ? `requested: ${t.tool_calls.join(", ")}` : "wrote the final answer"}</span>
                </div>
                {t.text && <pre className="tprev">{t.text}</pre>}
              </>
            )}
            {t.type === "tool" && (
              <>
                <div className="tline">
                  <span className="badge t-tool">tool</span>
                  <code>{t.name}</code>
                  <span className="muted">{t.ms} ms</span>
                  <span className={`badge ${t.ok ? "ok" : "bad"}`}>{t.ok ? "ok" : "error"}</span>
                </div>
                <div className="targs mono">args: {short(t.args || {})}</div>
                {t.error && <div className="terr">{t.error}</div>}
                <pre className="tprev">{t.result}</pre>
              </>
            )}
            {t.type === "error" && (
              <div className="tline">
                <span className="badge bad">error</span>
                <span>
                  {t.kind}: {t.error}
                </span>
              </div>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

// ---- unified diff ----
export function Diff({ diff }) {
  if (!diff) return null;
  return (
    <pre className="diff" tabIndex={0} aria-label="Proposed change">
      {diff.split("\n").map((l, i) => {
        let c = "";
        if (l.startsWith("+++") || l.startsWith("---")) c = "d-file";
        else if (l.startsWith("@@")) c = "d-hunk";
        else if (l.startsWith("+")) c = "d-add";
        else if (l.startsWith("-")) c = "d-del";
        return (
          <span key={i} className={`dl ${c}`}>
            {l || " "}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export function Pill({ children }) {
  return <span className="chip">{children}</span>;
}
