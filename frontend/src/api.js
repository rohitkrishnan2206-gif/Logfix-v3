// Thin fetch wrapper. Token lives in localStorage (falls back to memory if storage is blocked).
const KEY = "logfix_token";
let memToken = null;

export function getToken() {
  try {
    return localStorage.getItem(KEY) ?? memToken;
  } catch {
    return memToken;
  }
}
export function setToken(t) {
  memToken = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* storage blocked: memory only */
  }
}
export function clearToken() {
  memToken = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

// App registers these so api.js can trigger logout / forced password change.
export const hooks = { onUnauthorized() {}, onPasswordChange() {} };

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function detailText(d) {
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  return JSON.stringify(d);
}

export async function api(path, { method = "GET", body, responseType = "json" } = {}) {
  const headers = {};
  const t = getToken();
  if (t) headers.Authorization = "Bearer " + t;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Cannot reach the server");
  }
  if (!res.ok) {
    let detail = res.statusText || "Request failed";
    try {
      const j = await res.json();
      detail = detailText(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401 && !path.startsWith("/api/auth/login")) hooks.onUnauthorized();
    if (res.status === 403 && detail === "password_change_required") {
      hooks.onPasswordChange();
      detail = "You must change your password first.";
    }
    throw new ApiError(res.status, detail);
  }
  if (responseType === "text") return res.text();
  return res.json();
}

// Open the HTML incident report with the auth header (a plain link cannot send it).
export async function openReport(incidentId) {
  const w = window.open("about:blank", "_blank"); // open synchronously so popup blockers allow it
  try {
    const html = await api(`/api/incidents/${incidentId}/report.html`, { responseType: "text" });
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    if (w) w.location.href = url;
    else window.location.href = url;
  } catch (e) {
    if (w) w.close();
    throw e;
  }
}

// Download a file that needs the auth header (links cannot send it).
export async function download(path, filename) {
  const headers = {};
  const t = getToken();
  if (t) headers.Authorization = "Bearer " + t;
  let res;
  try { res = await fetch(path, { headers }); } catch { throw new ApiError(0, "Cannot reach the server"); }
  if (!res.ok) {
    let d = res.statusText;
    try { d = detailText((await res.json()).detail); } catch { /* not json */ }
    throw new ApiError(res.status, d);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// POST and read a text/event-stream response. onEvent(name, data) is called per event.
export async function streamPost(path, body, onEvent, signal) {
  const headers = { "Content-Type": "application/json" };
  const t = getToken();
  if (t) headers.Authorization = "Bearer " + t;
  let res;
  try { res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body), signal }); }
  catch (e) { if (e.name === "AbortError") return; throw new ApiError(0, "Cannot reach the server"); }
  if (!res.ok) {
    let d = res.statusText;
    try { d = detailText((await res.json()).detail); } catch { /* not json */ }
    if (res.status === 401) hooks.onUnauthorized();
    throw new ApiError(res.status, d);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let ev = "message", data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) { try { onEvent(ev, JSON.parse(data)); } catch { /* ignore malformed block */ } }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") throw e;
  }
}
