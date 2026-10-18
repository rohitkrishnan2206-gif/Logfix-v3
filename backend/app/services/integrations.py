"""Outbound integrations (Slack/Teams, PagerDuty, email, ServiceNow). Secrets are stored encrypted and
never returned to the browser; the API only reports whether one is set."""
from __future__ import annotations

from typing import Any

from ..context import AppContext
from .notify import validate_webhook

PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
KEY = "integrations"

DEFAULTS: dict[str, dict[str, Any]] = {
    "slack": {"enabled": False, "url": "", "min_priority": "P3"},
    "pagerduty": {"enabled": False, "routing_key": "", "min_priority": "P2"},
    "email": {"enabled": False, "host": "", "port": 587, "username": "", "password": "", "from_addr": "", "to": [], "starttls": True, "min_priority": "P2"},
    "servicenow": {"enabled": False, "instance_url": "", "username": "", "password": "", "min_priority": "P2"},
}
SECRET_FIELDS = {"slack": ["url"], "pagerduty": ["routing_key"], "email": ["password"], "servicenow": ["password"]}


def _load(actx: AppContext) -> dict:
    stored = actx.db.get_setting(KEY, {}) or {}
    out = {}
    for name, d in DEFAULTS.items():
        cur = dict(d)
        cur.update(stored.get(name) or {})
        out[name] = cur
    return out


def get_secrets(actx: AppContext) -> dict:
    """Full config with decrypted secrets, for server-side use only."""
    cfg = _load(actx)
    for name, fields in SECRET_FIELDS.items():
        dec = actx.secrets.decrypt(cfg[name].pop("secrets_enc", None))
        for f in fields:
            cfg[name][f] = dec.get(f, "")
    # Backwards compatible: the older single detection webhook acts as Slack when none is configured.
    if not cfg["slack"]["url"]:
        legacy = (actx.db.get_setting("detection", {}) or {}).get("webhook_url", "")
        if legacy:
            cfg["slack"].update(url=legacy, enabled=True)
    return cfg


def get_integrations(actx: AppContext) -> dict:
    """Safe view: secrets replaced by has_* booleans."""
    cfg = get_secrets(actx)
    out = {}
    for name, c in cfg.items():
        c = dict(c)
        for f in SECRET_FIELDS[name]:
            c[f"has_{f}"] = bool(c.pop(f, ""))
        out[name] = c
    return out


def update_integration(actx: AppContext, name: str, patch: dict) -> dict:
    if name not in DEFAULTS:
        raise ValueError("unknown integration")
    stored = actx.db.get_setting(KEY, {}) or {}
    cur = dict(stored.get(name) or {})
    secrets_cur = actx.secrets.decrypt(cur.get("secrets_enc"))
    for k, v in patch.items():
        if k not in DEFAULTS[name]:
            continue
        if k in SECRET_FIELDS[name]:
            if v is None or v == "":
                continue            # blank = keep existing
            secrets_cur[k] = v
        else:
            cur[k] = v
    if name == "slack" and secrets_cur.get("url"):
        err = validate_webhook(secrets_cur["url"])
        if err:
            raise ValueError(err)
    if name == "servicenow" and cur.get("instance_url"):
        u = cur["instance_url"].strip().rstrip("/")
        if not u.startswith("https://"):
            raise ValueError("ServiceNow instance URL must start with https://")
        err = validate_webhook(u)
        if err:
            raise ValueError(err.replace("Webhook", "Instance"))
        cur["instance_url"] = u
    if name == "email" and isinstance(cur.get("to"), str):
        cur["to"] = [x.strip() for x in cur["to"].split(",") if x.strip()]
    if cur.get("min_priority") not in PRIORITY_ORDER:
        cur["min_priority"] = DEFAULTS[name]["min_priority"]
    cur["secrets_enc"] = actx.secrets.encrypt(secrets_cur)
    stored[name] = cur
    actx.db.set_setting(KEY, stored)
    return get_integrations(actx)[name]


def priority_ok(priority: str | None, min_priority: str) -> bool:
    return PRIORITY_ORDER.get(priority or "P4", 4) <= PRIORITY_ORDER.get(min_priority, 3)
