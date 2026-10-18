import { useCallback, useEffect, useRef, useState } from "react";
import { api, clearToken, getToken, hooks } from "./api.js";
import { can, Spinner, useInterval, useRoute } from "./ui.jsx";
import { BrandMark, GlobalSearch, Icon, NAV, useClickAway } from "./shell.jsx";
import { ChangePassword, Login } from "./pages/Auth.jsx";
import Overview from "./pages/Overview.jsx";
import Incidents from "./pages/Incidents.jsx";
import Agents from "./pages/Agents.jsx";
import Logs from "./pages/Logs.jsx";
import Sources from "./pages/Sources.jsx";
import Chat from "./pages/Chat.jsx";
import Knowledge from "./pages/Knowledge.jsx";
import Code from "./pages/Code.jsx";
import Admin from "./pages/Admin.jsx";

const COMPONENTS = { overview: Overview, incidents: Incidents, agents: Agents, logs: Logs, sources: Sources, chat: Chat, knowledge: Knowledge, code: Code, admin: Admin };

function readTheme() {
  try { return localStorage.getItem("logfix.theme") || "light"; } catch { return "light"; }
}

export default function App() {
  const [user, setUser] = useState(null);
  const [boot, setBoot] = useState(true);
  const [mustChange, setMustChange] = useState(false);
  const [showChange, setShowChange] = useState(false);
  const [theme, setTheme] = useState(readTheme);
  const [expanded, setExpanded] = useState(false);
  const [menu, setMenu] = useState(false);
  const [openCount, setOpenCount] = useState(0);
  const [nonce, setNonce] = useState(0);
  const menuRef = useRef(null);
  const route = useRoute();
  useClickAway(menuRef, () => setMenu(false), menu);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("logfix.theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);

  useEffect(() => {
    const f = () => setNonce((n) => n + 1); // global search handed a query to a page: remount it
    window.addEventListener("logfix-handoff", f);
    return () => window.removeEventListener("logfix-handoff", f);
  }, []);

  useEffect(() => {
    hooks.onUnauthorized = () => { clearToken(); setUser(null); };
    hooks.onPasswordChange = () => setMustChange(true);
    if (getToken()) {
      api("/api/auth/me")
        .then((u) => { setUser(u); setMustChange(u.must_change); })
        .catch(() => {})
        .finally(() => setBoot(false));
    } else {
      setBoot(false);
    }
  }, []);

  const loadCount = useCallback(() => {
    if (!user || mustChange) return;
    api("/api/incidents?status=open&limit=100").then((r) => setOpenCount(r.length)).catch(() => {});
  }, [user, mustChange]);
  useEffect(loadCount, [loadCount, route.page]);
  useInterval(loadCount, 20000, !!user && !mustChange);

  const logout = () => { clearToken(); setUser(null); setMustChange(false); setShowChange(false); setMenu(false); };

  if (boot) return <Spinner label="Starting" />;
  if (!user) return <Login onLogin={(u) => { setUser(u); setMustChange(u.must_change); }} />;
  if (mustChange || showChange)
    return (
      <ChangePassword
        forced={mustChange}
        onDone={async () => {
          try { setUser(await api("/api/auth/me")); } catch { /* handled by hooks */ }
          setMustChange(false);
          setShowChange(false);
        }}
        onCancel={() => setShowChange(false)}
        onLogout={logout}
      />
    );

  const visible = NAV.filter((p) => !p.role || can(user, p.role));
  const current = visible.find((p) => p.id === route.page) || visible[0];
  const Page = COMPONENTS[current.id];

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <button className="icon-btn" aria-label={expanded ? "Collapse navigation" : "Expand navigation"} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          <Icon name="menu" />
        </button>
        <a className="brandlink" href="#/overview" aria-label="LogFix AI home"><BrandMark /><span className="bn">LogFix AI</span></a>
        <GlobalSearch />
        <div className="top-actions">
          <a className="icon-btn" href="#/chat" title="Ask LogFix (chat)" aria-label="Ask LogFix"><Icon name="term" /></a>
          <a className="icon-btn" href="#/incidents" title={`${openCount} open incidents`} aria-label={`${openCount} open incidents`}>
            <Icon name="bell" />
            {openCount > 0 && <span className="dot-badge">{openCount > 99 ? "99+" : openCount}</span>}
          </a>
          <button className="icon-btn" aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`} title="Toggle theme" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
            <Icon name={theme === "light" ? "moon" : "sun"} />
          </button>
          {can(user, "admin") && <a className="icon-btn" href="#/admin" title="Administration" aria-label="Administration"><Icon name="gear" /></a>}
          <a className="icon-btn" href="/api/docs" target="_blank" rel="noopener noreferrer" title="API reference" aria-label="API reference (opens in a new tab)"><Icon name="help" /></a>
          <div className="menu-wrap" ref={menuRef}>
            <button className="userbtn" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>
              <span className="ut"><span className="un">{user.username}</span><span className="ur">{user.role}</span></span>
              <span className="avatar" aria-hidden="true">{user.username.slice(0, 2)}</span>
            </button>
            {menu && (
              <div className="menu-pop" role="menu">
                <div className="mh"><b>{user.username}</b>{user.role} role</div>
                <button className="mi" role="menuitem" onClick={() => { setMenu(false); setShowChange(true); }}>Change password</button>
                <button className="mi" role="menuitem" onClick={logout}>Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className={`body ${expanded ? "expanded" : ""}`}>
        <nav className="rail" aria-label="Main">
          <a className="add" href="#/sources" title="Add a log source" aria-label="Add a log source"><Icon name="plus" /><span className="lbl">Add log source</span></a>
          <div className="sep" />
          {visible.map((p) => (
            <a key={p.id} href={`#/${p.id}`} title={p.label} aria-label={p.label} aria-current={p.id === current.id ? "page" : undefined}>
              <Icon name={p.icon} /><span className="lbl">{p.label}</span>
            </a>
          ))}
        </nav>
        <main className="main" id="main" tabIndex={-1}>
          {current.id !== "overview" && (
            <div className="crumbs"><a href="#/overview">Home</a> &rsaquo; {current.label}</div>
          )}
          <Page user={user} route={route} key={`${current.id}-${nonce}`} />
        </main>
      </div>
    </div>
  );
}
