// App shell pieces: icons, top bar, left rail, global search. Own icons (simple stroke paths).
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { handoff, nav, relTime, SevBadge } from "./ui.jsx";

const P = {
  home: "M3 11.5 12 4l9 7.5M5.5 10v9.5h13V10M10 19.5v-5h4v5",
  incidents: "M12 3.5 2.8 19.5h18.4L12 3.5ZM12 10v4.5M12 17.2v.1",
  logs: "M4 5.5h16M4 10h16M4 14.5h10M4 19h7",
  sources: "M6.5 18.5a4 4 0 0 1-.5-7.97 6 6 0 0 1 11.6-1.2A4.6 4.6 0 0 1 17.5 18.5h-11ZM9 18.5v2M15 18.5v2",
  agents: "M12 3v3M12 18v3M3 12h3M18 12h3M7 7l2 2M15 15l2 2M17 7l-2 2M9 15l-2 2M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z",
  chat: "M4 5h16v11H9.5L5 20v-4H4V5ZM8 9.5h8M8 12.5h5",
  book: "M5 4.5h10.5a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3V4.5ZM5 17a3 3 0 0 1 3-3h10.5M9 8h6",
  code: "m8.5 7-5 5 5 5M15.5 7l5 5-5 5M13.5 5l-3 14",
  admin: "M12 3 4.5 6v5.5c0 4.4 3.1 8 7.5 9.5 4.4-1.5 7.5-5.1 7.5-9.5V6L12 3ZM9 12l2.2 2.2L15.5 10",
  plus: "M12 5v14M5 12h14",
  bell: "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15L6 16.5ZM10 20.5h4",
  search: "M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13ZM15.5 15.5 20 20",
  menu: "M4 7h16M4 12h16M4 17h16",
  sun: "M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.1",
  term: "M4 5.5h16v13H4v-13ZM7.5 10l3 2.5-3 2.5M12.5 15h4",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14.3 3h-4l-.4 2.7a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.4 2.7h4l.4-2.7a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z",
  pulse: "M3 12h4l2.5-6 4 12 2.5-6H21",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
};

export function Icon({ name }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] || P.pulse} />
    </svg>
  );
}

export function BrandMark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#ffffff26" />
      <path d="M6.5 21.5l6-8 5 5 8-11" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Pages shown in the rail and the home tiles. color = tile icon color.
export const NAV = [
  { id: "overview", label: "Home", icon: "home" },
  { id: "incidents", label: "Incidents", icon: "incidents", color: "#c4314b" },
  { id: "agents", label: "Agents", icon: "agents", color: "#d83b01" },
  { id: "logs", label: "Logs", icon: "logs", color: "#0a64c2" },
  { id: "sources", label: "Log sources", icon: "sources", color: "#0e7a8a" },
  { id: "chat", label: "Chat", icon: "chat", color: "#7a3fc9" },
  { id: "knowledge", label: "Knowledge base", icon: "book", color: "#b45309" },
  { id: "code", label: "Code assistant", icon: "code", color: "#137333" },
  { id: "admin", label: "Administration", icon: "admin", color: "#505a64", role: "admin" },
];

export function useClickAway(ref, onAway, active) {
  useEffect(() => {
    if (!active) return undefined;
    const f = (e) => { if (ref.current && !ref.current.contains(e.target)) onAway(); };
    const k = (e) => { if (e.key === "Escape") onAway(); };
    document.addEventListener("mousedown", f);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", f); document.removeEventListener("keydown", k); };
  }, [active, onAway, ref]);
}

function bump() { window.dispatchEvent(new Event("logfix-handoff")); }

export function GlobalSearch() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const input = useRef(null);
  const seq = useRef(0);
  useClickAway(wrap, () => setOpen(false), open);

  useEffect(() => {
    const f = (e) => {
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setRes(null); return undefined; }
    const mine = ++seq.current;
    const t = setTimeout(async () => {
      const enc = encodeURIComponent(term);
      const [inc, logs, kb] = await Promise.all([
        api(`/api/incidents?q=${enc}&limit=5`).catch(() => []),
        api(`/api/logs?q=${enc}&minutes=1440&limit=5`).catch(() => ({ logs: [], total: 0 })),
        api(`/api/kb/search?q=${enc}`).catch(() => ({ results: [] })),
      ]);
      if (mine === seq.current) setRes({ inc, logs, kb: kb.results || [], term });
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const go = (kind) => { handoff[kind] = q.trim(); bump(); setOpen(false); };
  const empty = res && !res.inc.length && !res.logs.logs.length && !res.kb.length;
  return (
    <div className="gsearch" ref={wrap} role="search">
      <input
        ref={input}
        aria-label="Search incidents, logs and runbooks"
        placeholder="Search incidents, logs and runbooks  ( / )"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && q.trim()) { go("logsQ"); nav("logs"); }
        }}
      />
      <svg className="gs-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d={P.search} /></svg>
      {open && q.trim().length >= 2 && (
        <div className="gs-pop">
          {!res && <div className="gs-none">Searching...</div>}
          {empty && <div className="gs-none">No incidents, logs or runbooks match "{res.term}".</div>}
          {res && res.inc.length > 0 && (<><h3>Incidents</h3>{res.inc.map((i) => (
            <a key={i.id} href={`#/incidents/${i.id}`} onClick={() => setOpen(false)}><SevBadge sev={i.severity} /><span className="t">{i.title}</span></a>
          ))}</>)}
          {res && res.logs.logs.length > 0 && (<><h3>Logs (last 24h, {res.logs.total} matches)</h3>{res.logs.logs.map((l) => (
            <a key={l.id} href="#/logs" onClick={() => go("logsQ")}><SevBadge sev={l.severity} /><span className="t mono">{l.message}</span><span className="muted">{relTime(l.ts)}</span></a>
          ))}</>)}
          {res && res.kb.length > 0 && (<><h3>Runbooks (keyword match)</h3>{res.kb.map((d) => (
            <a key={d.doc_id} href="#/knowledge" onClick={() => go("kbQ")}><span className="t">{d.title}</span></a>
          ))}</>)}
        </div>
      )}
    </div>
  );
}
