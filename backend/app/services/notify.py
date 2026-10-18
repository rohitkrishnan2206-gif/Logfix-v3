"""Optional Slack / Teams incoming-webhook notification (both accept {"text": ...})."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx


def validate_webhook(url: str) -> str | None:
    """Return an error string if the URL is unsafe. Admin-set, but still blocks SSRF to
    internal addresses and plain http."""
    if not url:
        return None
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        return "Webhook must be an https URL"
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith(".internal") or host.endswith(".local"):
        return "Webhook host is not allowed"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "Webhook IP is not allowed"
    except ValueError:
        pass
    return None


def notify_incident(webhook_url: str, incident: dict, transport: httpx.BaseTransport | None = None) -> bool:
    if not webhook_url or validate_webhook(webhook_url):
        return False
    text = f"[LogFix AI] {incident['severity']} incident #{incident['id']}: {incident['title']} ({incident['event_count']} events)"
    try:
        with httpx.Client(transport=transport, timeout=10) as c:
            return c.post(webhook_url, json={"text": text}).status_code < 300
    except httpx.HTTPError:
        return False


# ---------------- multi-channel notifier ----------------
import smtplib
from email.message import EmailMessage

from ..redact import redact


def _summary_line(inc: dict, link: str = "") -> str:
    return redact(f"[LogFix {inc.get('priority') or ''}] {inc['severity']} #{inc['id']} {inc['title']} "
                  f"({inc['event_count']} events){' ' + link if link else ''}").strip()


def send_slack(url: str, inc: dict, extra: str = "", link: str = "", transport=None) -> str:
    err = validate_webhook(url)
    if err:
        raise RuntimeError(err)
    text = _summary_line(inc, link) + (f"\n{redact(extra)[:800]}" if extra else "")
    with httpx.Client(transport=transport, timeout=10) as c:
        r = c.post(url, json={"text": text})
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}")
    return "sent"


def send_pagerduty(routing_key: str, inc: dict, link: str = "", transport=None) -> str:
    sev = {"P1": "critical", "P2": "error", "P3": "warning", "P4": "info"}.get(inc.get("priority"), "error")
    body = {"routing_key": routing_key, "event_action": "trigger", "dedup_key": f"logfix-{inc['fingerprint']}-{inc.get('service')}",
            "payload": {"summary": _summary_line(inc)[:1000], "source": inc.get("service") or "logfix", "severity": sev,
                        "custom_details": {"score": inc.get("score"), "category": inc.get("category"), "events": inc["event_count"]}}}
    if link:
        body["links"] = [{"href": link, "text": "Open in LogFix"}]
    with httpx.Client(transport=transport, timeout=10) as c:
        r = c.post("https://events.pagerduty.com/v2/enqueue", json=body)
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    return "triggered"


def send_email(cfg: dict, inc: dict, body: str, link: str = "", smtp_factory=None) -> str:
    if not cfg.get("host") or not cfg.get("to") or not cfg.get("from_addr"):
        raise RuntimeError("email is not fully configured")
    msg = EmailMessage()
    msg["Subject"] = _summary_line(inc)[:200]
    msg["From"], msg["To"] = cfg["from_addr"], ", ".join(cfg["to"])
    msg.set_content(redact(body) + (f"\n\nOpen: {link}" if link else ""))
    factory = smtp_factory or (lambda: smtplib.SMTP(cfg["host"], int(cfg.get("port") or 587), timeout=15))
    with factory() as s:
        if cfg.get("starttls", True) and not smtp_factory:
            s.starttls()
        if cfg.get("username") and cfg.get("password"):
            s.login(cfg["username"], cfg["password"])
        s.send_message(msg)
    return f"sent to {len(cfg['to'])} recipient(s)"
