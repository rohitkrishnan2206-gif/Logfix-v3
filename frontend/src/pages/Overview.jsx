import { api } from "../api.js";
import { Icon, NAV } from "../shell.jsx";
import { can, Card, CloudBadge, Empty, ErrorBox, fmtClock, LlmBadge, relTime, SevBadge, Spinner, StatusBadge, useInterval, useLoad } from "../ui.jsx";

function Timeline({ data }) {
  const W = 640, H = 170, L = 30, B = 22, T = 10, R = 6;
  const max = Math.max(1, ...data.map((d) => d.errors));
  const bw = (W - L - R) / data.length;
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const total = data.reduce((a, d) => a + d.errors, 0);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Errors per 5 minutes over the last hour, ${total} in total`}>
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line className="grid-l" x1={L} x2={W - R} y1={y(max * f)} y2={y(max * f)} />
          <text x={L - 4} y={y(max * f) + 3} textAnchor="end">{Math.round(max * f)}</text>
        </g>
      ))}
      {data.map((d, i) => {
        const h = (H - T - B) * (d.errors / max);
        return (
          <rect key={d.start} className={`bar ${d.errors >= max * 0.6 && d.errors > 0 ? "hot" : ""}`} x={L + i * bw + 2} y={H - B - h} width={Math.max(2, bw - 4)} height={h} rx="2">
            <title>{`${fmtClock(d.start)}: ${d.errors} errors`}</title>
          </rect>
        );
      })}
      {[0, 6, 12].map((i) => (
        <text key={i} x={L + i * bw + bw / 2} y={H - 6} textAnchor="middle">{fmtClock(data[i]?.start)}</text>
      ))}
    </svg>
  );
}

const PROMO = [
  { icon: "incidents", color: "#c4314b", head: "Investigate", text: " incidents with an AI agent that cites its evidence", link: "Open incidents", to: "#/incidents" },
  { icon: "sources", color: "#0e7a8a", head: "Monitor", text: " AWS, Azure and GCP logs on a schedule", link: "Log sources", to: "#/sources" },
  { icon: "chat", color: "#7a3fc9", head: "Ask", text: " questions about your logs in plain English", link: "Open chat", to: "#/chat" },
  { icon: "book", color: "#b45309", head: "Search", text: " runbooks and documentation the agents use", link: "Knowledge base", to: "#/knowledge" },
  { icon: "code", color: "#137333", head: "Explore", text: " a code repository and review proposed fixes", link: "Code assistant", to: "#/code" },
];

export default function Overview({ user }) {
  const { data, error, loading, reload } = useLoad(() => api("/api/overview"));
  const inc = useLoad(() => api("/api/incidents?limit=6"));
  useInterval(() => { reload(true); inc.reload(true); }, 10000);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorBox error={error} onRetry={reload} />;

  const open = data.open_incidents.reduce((a, r) => a + r.n, 0);
  const maxSvc = Math.max(1, ...data.top_services.map((s) => s.errors));
  const tiles = NAV.filter((p) => p.id !== "overview" && (!p.role || can(user, p.role)));
  const recent = [
    ...(inc.data || []).map((i) => ({ key: `i${i.id}`, name: i.title, type: "Incident", badge: <SevBadge sev={i.severity} />, when: i.last_seen, to: `#/incidents/${i.id}`, icon: "incidents" })),
    ...data.sources.map((s) => ({ key: `s${s.id}`, name: s.name, type: "Log source", badge: <CloudBadge cloud={s.cloud} />, when: s.last_collected_at, to: "#/sources", icon: "sources" })),
  ].sort((a, b) => new Date(b.when || 0) - new Date(a.when || 0)).slice(0, 8);

  return (
    <>
      <ErrorBox error={error} />
      <div className="sec-h"><h2 className="sec-t">Services</h2><span className="right"><LlmBadge llm={data.llm} /></span></div>
      <div className="tiles">
        {tiles.map((p) => (
          <a key={p.id} className="tile" href={`#/${p.id}`} style={{ "--tc": p.color }}>
            <span className="ico"><Icon name={p.icon} /></span>
            {p.id === "incidents" && open > 0 && <span className="cnt" aria-label={`${open} open`}>{open}</span>}
            {p.label}
          </a>
        ))}
        <a className="tile" href="#/sources" style={{ "--tc": "#107c10" }}><span className="ico"><Icon name="plus" /></span>Add log source</a>
      </div>

      {data.llm.is_real === false && (
        <div className="alert alert-warn">
          <span>No AI model is configured. Investigations and chat use a fixed-rule fallback, not real reasoning.{can(user, "admin") ? " Connect Bedrock or Azure OpenAI under Administration." : " Ask an admin to connect a model."}</span>
        </div>
      )}

      <div className="sec-h"><h2 className="sec-t">Make the most of LogFix AI</h2></div>
      <div className="promo">
        {PROMO.map((p) => (
          <a key={p.head} className="pc" href={p.to} style={{ "--tc": p.color }}>
            <span className="ico"><Icon name={p.icon} /></span>
            <span><b>{p.head}</b>{p.text}</span>
            <span className="lk">{p.link} &rsaquo;</span>
          </a>
        ))}
      </div>

      <div className="home-cols">
        <div className="res">
          <div className="sec-h"><h2 className="sec-t">Recent resources</h2><a href="#/incidents">See all incidents &rsaquo;</a><a href="#/sources">See all sources &rsaquo;</a></div>
          {recent.length === 0 ? <Empty>Nothing yet. Add a log source to get started.</Empty> : (
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>Name</th><th>Type</th><th>Detail</th><th>Last activity</th></tr></thead>
              <tbody>{recent.map((r) => (
                <tr key={r.key}>
                  <td><a className="ri" href={r.to}><Icon name={r.icon} /><span>{r.name.length > 70 ? r.name.slice(0, 70) + "..." : r.name}</span></a></td>
                  <td className="nowrap">{r.type}</td><td>{r.badge}</td><td className="nowrap muted">{relTime(r.when)}</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </div>
        <div className="links">
          <div className="sec-h"><h2 className="sec-t">Useful links</h2></div>
          <a href="#/sources">Connect a cloud log source<small>AWS CloudWatch, Azure Log Analytics, GCP Cloud Logging. Read-only.</small></a>
          <a href="#/knowledge">Add your own runbooks<small>The agents search these while investigating.</small></a>
          <a href="#/code">Point the assistant at a repository<small>Edits are proposals; an admin approves each one.</small></a>
          <a href="/api/docs" target="_blank" rel="noopener noreferrer">API reference<small>Every action in this console is also an API call.</small></a>
          {can(user, "admin") && <a href="#/admin">Audit log and model settings<small>Who did what, and which AI provider is active.</small></a>}
        </div>
      </div>

      <div className="sec-h"><h2 className="sec-t">Health, last 60 minutes</h2></div>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Card className="stat"><div className="v">{data.logs_last_hour}</div><div className="l">logs collected</div></Card>
        <Card className={`stat ${data.errors_last_hour ? "bad" : ""}`}><div className="v">{data.errors_last_hour}</div><div className="l">errors (HIGH + CRITICAL)</div></Card>
        <Card className="stat">
          <div className="v">{open}</div><div className="l">open incidents</div>
          <div style={{ marginTop: 6 }}>{data.open_incidents.map((r) => <span key={r.severity} style={{ marginRight: 6 }}><SevBadge sev={r.severity} /> {r.n}</span>)}</div>
        </Card>
        <Card className="stat"><div className="v">{data.total_incidents}</div><div className="l">incidents in history</div></Card>
      </div>
      <div className="grid g21" style={{ marginBottom: 14 }}>
        <Card title="Errors per 5 minutes"><Timeline data={data.timeline} /></Card>
        <Card title="Top services by errors">
          {data.top_services.length === 0 ? <Empty>No errors in the last hour.</Empty> : data.top_services.map((s) => (
            <div className="svc" key={s.service}>
              <span>{s.service}</span><span className="muted">{s.errors}</span>
              <div style={{ gridColumn: "1 / -1" }}><div className="hbar" style={{ width: `${(s.errors / maxSvc) * 100}%` }} /></div>
            </div>
          ))}
        </Card>
      </div>
      <Card title="Source status" actions={<a href="#/sources">Manage</a>}>
        {data.sources.length === 0 ? <Empty>No sources yet.</Empty> : (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>Name</th><th>Cloud</th><th>Status</th><th>Last collected</th></tr></thead>
            <tbody>{data.sources.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td><td><CloudBadge cloud={s.cloud} /></td>
                <td>{s.last_status ? <span className={`badge ${s.last_status === "ok" ? "ok" : "bad"}`}>{s.last_status}</span> : <span className="muted">not collected yet</span>}</td>
                <td className="muted">{relTime(s.last_collected_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </>
  );
}
