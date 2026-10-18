"""Azure Log Analytics connector (KQL over REST). Read-only by construction: we only
call the /query endpoint with a token scoped to api.loganalytics.io."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import Connector, ConnectorError, LogRecord, http_error, with_retries

_SEV = {0: "CRITICAL", 1: "CRITICAL", 2: "CRITICAL", 3: "ERROR", 4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_FORBIDDEN_KQL = re.compile(r"(?i)(^|[\s|(;])\.(set|append|drop|create|delete|alter|ingest|move|replace|purge)\b|\b(externaldata|evaluate|invoke)\b")

DEFAULT_QUERY = """union isfuzzy=true
  (Syslog | project TimeGenerated, Computer, Message=SyslogMessage, Level=SeverityLevel, Proc=ProcessName, Id=tostring(TenantId)),
  (Event | project TimeGenerated, Computer, Message=RenderedDescription, Level=EventLevelName, Proc=Source, Id=tostring(EventID))
| where TimeGenerated >= datetime({since}) and TimeGenerated < datetime({until})
{computer_filter}
| order by TimeGenerated asc
| take {limit}"""


class AzureConnector(Connector):
    cloud = "azure"

    def __init__(self, config: dict, secrets: dict, transport: httpx.BaseTransport | None = None, sleep=None):
        self.workspace_id = (config.get("workspace_id") or "").strip()
        self.tenant_id = (config.get("tenant_id") or "").strip()
        self.client_id = (config.get("client_id") or "").strip()
        self.client_secret = secrets.get("client_secret", "")
        self.computer = (config.get("computer") or "").strip()
        self.custom_query = (config.get("kql") or "").strip()
        self._http = httpx.Client(transport=transport, timeout=30)
        self._sleep = sleep
        self._token: tuple[str, float] | None = None

    def _check(self) -> None:
        for k in ("workspace_id", "tenant_id", "client_id"):
            if not getattr(self, k):
                raise ConnectorError("config", f"Missing Azure setting: {k}")
        if not self.client_secret:
            raise ConnectorError("config", "Missing Azure client secret")
        if self.computer and not _SAFE_NAME.match(self.computer):
            raise ConnectorError("config", "Invalid computer name")
        if self.custom_query and _FORBIDDEN_KQL.search(self.custom_query):
            raise ConnectorError("config", "Custom KQL contains a disallowed operator")

    def _retry(self, fn):
        kw = {"sleep": self._sleep} if self._sleep else {}
        return with_retries(fn, **kw)

    def _get_token(self) -> str:
        import time

        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]

        def call():
            try:
                r = self._http.post(
                    f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "https://api.loganalytics.io/.default",
                    },
                )
            except httpx.TransportError as e:
                raise ConnectorError("transient", f"Network error: {e}") from e
            if r.status_code == 400:  # bad tenant/secret comes back as 400/401
                raise ConnectorError("auth", f"Azure AD rejected credentials: {r.text[:200]}")
            if r.status_code != 200:
                raise http_error(r.status_code, r.text)
            return r.json()

        data = self._retry(call)
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        return self._token[0]

    def _query(self, kql: str) -> list[dict]:
        token = self._get_token()

        def call():
            try:
                r = self._http.post(
                    f"https://api.loganalytics.io/v1/workspaces/{self.workspace_id}/query",
                    json={"query": kql},
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TransportError as e:
                raise ConnectorError("transient", f"Network error: {e}") from e
            if r.status_code != 200:
                raise http_error(r.status_code, r.text)
            return r.json()

        body = self._retry(call)
        tables = body.get("tables") or []
        if not tables:
            return []
        cols = [c["name"] for c in tables[0]["columns"]]
        return [dict(zip(cols, row)) for row in tables[0]["rows"]]

    def test(self) -> dict[str, Any]:
        self._check()
        rows = self._query("print ok=1")
        return {"ok": bool(rows), "workspace_id": self.workspace_id}

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def fetch(self, since: datetime, until: datetime, limit: int = 2000) -> list[LogRecord]:
        self._check()
        if self.custom_query:
            kql = self.custom_query.format(since=self._iso(since), until=self._iso(until), limit=limit)
        else:
            cf = f'| where Computer == "{self.computer}"' if self.computer else ""
            kql = DEFAULT_QUERY.format(since=self._iso(since), until=self._iso(until), computer_filter=cf, limit=limit)
        out: list[LogRecord] = []
        for i, row in enumerate(self._query(kql)):
            ts_raw = row.get("TimeGenerated")
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            level = row.get("Level")
            if isinstance(level, int) or (isinstance(level, str) and level.isdigit()):
                level = _SEV.get(int(level))
            msg = str(row.get("Message") or "").rstrip()
            if not msg:
                continue
            out.append(
                LogRecord(
                    uid=f"{ts_raw}|{row.get('Computer')}|{hashlib.sha1(str(msg).encode()).hexdigest()[:10]}",
                    ts=ts.astimezone(timezone.utc),
                    message=msg,
                    level=level,
                    service=row.get("Proc") or None,
                    host=row.get("Computer"),
                    raw={"event_id": row.get("Id")},
                )
            )
        return out
