// Tiny safe markdown renderer (no HTML injection): headings, bold, italic, inline code, code blocks, lists, links.
import { useState } from "react";

function inline(text, key) {
  const out = [];
  const rx = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0, m, i = 0;
  while ((m = rx.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) out.push(<code key={`${key}-${i++}`}>{t.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={`${key}-${i++}`}>{t.slice(2, -2)}</strong>);
    else if (m[3]) out.push(<em key={`${key}-${i++}`}>{t.slice(1, -1)}</em>);
    else out.push(<a key={`${key}-${i++}`} href={m[5]} target="_blank" rel="noopener noreferrer">{t.slice(1, t.indexOf("]"))}</a>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function CodeBlock({ code }) {
  const [done, setDone] = useState(false);
  return (
    <pre>
      <button className="btn sm copy" onClick={() => { navigator.clipboard?.writeText(code).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); }).catch(() => {}); }}>{done ? "Copied" : "Copy"}</button>
      <code>{code}</code>
    </pre>
  );
}

export function Markdown({ text }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const blocks = [];
  let i = 0, k = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      i++;
      blocks.push(<CodeBlock key={k++} code={buf.join("\n")} />);
    } else if (/^#{1,4}\s/.test(l)) {
      blocks.push(<h4 key={k++}>{inline(l.replace(/^#{1,4}\s+/, ""), k)}</h4>);
      i++;
    } else if (/^\s*[-*]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ""));
      blocks.push(<ul key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ul>);
    } else if (/^\s*\d+[.)]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(<ol key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ol>);
    } else if (!l.trim()) {
      i++;
    } else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !lines[i].startsWith("```") && !/^#{1,4}\s/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) buf.push(lines[i++]);
      blocks.push(<p key={k++}>{inline(buf.join(" "), k)}</p>);
    }
  }
  return <div className="md">{blocks}</div>;
}
