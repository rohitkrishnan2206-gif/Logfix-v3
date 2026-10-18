"""GCP Cloud Logging connector. Service-account JWT (RS256) -> OAuth token ->
entries:list. Read-only scope only."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt

from .base import Connector, ConnectorError, LogRecord, http_error, with_retries

SCOPE = "https://www.googleapis.com/auth/logging.read"
_SEV = {"EMERGENCY": "CRITICAL", "ALERT": "CRITICAL", "CRITICAL": "CRITICAL", "ERROR": "ERROR",
        "WARNING": "WARNING", "NOTICE": "NOTICE", "INFO": "INFO", "DEBUG": "DEBUG", "DEFAULT": None}


class GcpConnector(Connector):
    cloud = "gcp"

    def __init__(self, config: dict, secrets: dict, transport: httpx.BaseTransport | None = None, sleep=None):
        self.project_id = (config.get("project_id") or "").strip()
        self.extra_filter = (config.get("filter") or "").strip()
        sa = secrets.get("service_account_json")
        if isinstance(sa, str):
            try:
                sa = json.loads(sa)
            except json.JSONDecodeError:
                sa = None
        self.sa: dict | None = sa
        self._http = httpx.Client(transport=transport, timeout=30)
        self._sleep = sleep
        self._token: tuple[str, float] | None = None

    def _check(self) -> None:
        if not self.project_id:
            raise ConnectorError("config", "Missing GCP project_id")
        if not self.sa or not self.sa.get("private_key") or not self.sa.get("client_email"):
            raise ConnectorError("config", "Missing or invalid service account JSON")

    def _retry(self, fn):
        kw = {"sleep": self._sleep} if self._sleep else {}
        return with_retries(fn, **kw)

    def _get_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        assert self.sa
        now = int(time.time())
        token_uri = self.sa.get("token_uri", "https://oauth2.googleapis.com/token")
        try:
            assertion = jwt.encode(
                {"iss": self.sa["client_email"], "scope": SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600},
                self.sa["private_key"],
                algorithm="RS256",
            )
        except Exception as e:  # noqa: BLE001
            raise ConnectorError("config", f"Cannot sign JWT with service account key: {e}") from e

        def call():
            try:
                r = self._http.post(
                    token_uri,
                    data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
                )
            except httpx.TransportError as e:
                raise ConnectorError("transient", f"Network error: {e}") from e
            if r.status_code in (400, 401):
                raise ConnectorError("auth", f"Google rejected credentials: {r.text[:200]}")
            if r.status_code != 200:
                raise http_error(r.status_code, r.text)
            return r.json()

        data = self._retry(call)
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        return self._token[0]

    def _list(self, flt: str, page_size: int, page_token: str | None) -> dict:
        token = self._get_token()
        body: dict[str, Any] = {
            "resourceNames": [f"projects/{self.project_id}"],
            "filter": flt,
            "orderBy": "timestamp asc",
            "pageSize": page_size,
        }
        if page_token:
            body["pageToken"] = page_token

        def call():
            try:
                r = self._http.post(
                    "https://logging.googleapis.com/v2/entries:list",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TransportError as e:
                raise ConnectorError("transient", f"Network error: {e}") from e
            if r.status_code != 200:
                raise http_error(r.status_code, r.text)
            return r.json()

        return self._retry(call)

    def test(self) -> dict[str, Any]:
        self._check()
        self._list("", 1, None)
        return {"ok": True, "project_id": self.project_id}

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def fetch(self, since: datetime, until: datetime, limit: int = 2000) -> list[LogRecord]:
        self._check()
        flt = f'timestamp >= "{self._iso(since)}" AND timestamp < "{self._iso(until)}"'
        if self.extra_filter:
            flt += f" AND ({self.extra_filter})"
        out: list[LogRecord] = []
        token = None
        while len(out) < limit:
            resp = self._list(flt, min(1000, limit - len(out)), token)
            for e in resp.get("entries", []):
                msg = e.get("textPayload")
                if msg is None and isinstance(e.get("jsonPayload"), dict):
                    jp = e["jsonPayload"]
                    msg = jp.get("message") or json.dumps(jp)[:2000]
                if msg is None and isinstance(e.get("protoPayload"), dict):
                    msg = e["protoPayload"].get("status", {}).get("message") or e["protoPayload"].get("methodName")
                if not msg:
                    continue
                try:
                    ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                res = e.get("resource", {})
                labels = res.get("labels", {})
                out.append(
                    LogRecord(
                        uid=e.get("insertId") or f"{e.get('timestamp')}|{hashlib.sha1(str(msg).encode()).hexdigest()[:10]}",
                        ts=ts.astimezone(timezone.utc),
                        message=str(msg).rstrip(),
                        level=_SEV.get(str(e.get("severity", "")).upper()),
                        service=labels.get("service_name") or labels.get("function_name") or res.get("type"),
                        host=labels.get("instance_id") or labels.get("pod_name"),
                        raw={"log_name": e.get("logName"), "resource_type": res.get("type")},
                    )
                )
            token = resp.get("nextPageToken")
            if not token:
                break
        return out[:limit]
