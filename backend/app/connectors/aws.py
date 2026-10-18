"""AWS CloudWatch Logs connector. Read-only: the boto3 client is wrapped so only an
allowlist of read calls can ever be invoked."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import Connector, ConnectorError, LogRecord, with_retries

ALLOWED_CALLS = {"filter_log_events", "describe_log_groups", "describe_log_streams"}

_THROTTLE = {"ThrottlingException", "Throttling", "TooManyRequestsException", "RequestLimitExceeded"}
_AUTH = {
    "AccessDeniedException", "UnrecognizedClientException", "InvalidSignatureException",
    "ExpiredTokenException", "InvalidClientTokenId", "AuthFailure", "AccessDenied",
}
_NOTFOUND = {"ResourceNotFoundException"}
_TRANSIENT = {"ServiceUnavailableException", "InternalFailure", "InternalServerError", "RequestTimeout"}


class ReadOnlyClient:
    def __init__(self, client: Any):
        self._c = client

    def __getattr__(self, name: str):
        if name not in ALLOWED_CALLS:
            raise PermissionError(f"AWS call '{name}' is not allowed: connector is read-only")
        return getattr(self._c, name)


def map_aws_error(exc: Exception) -> ConnectorError:
    if isinstance(exc, ConnectorError):
        return exc
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code", "")
    name = type(exc).__name__
    msg = str(exc)[:300]
    if code in _THROTTLE:
        return ConnectorError("throttle", f"{code}: {msg}")
    if code in _AUTH:
        return ConnectorError("auth", f"{code}: {msg}")
    if code in _NOTFOUND:
        return ConnectorError("notfound", f"{code}: {msg}")
    if code in _TRANSIENT or name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError"):
        return ConnectorError("transient", f"{code or name}: {msg}")
    if name in ("NoCredentialsError", "PartialCredentialsError"):
        return ConnectorError("auth", f"No usable AWS credentials: {msg}")
    return ConnectorError("unknown", f"{code or name}: {msg}")


def make_logs_client(region: str, creds: dict) -> Any:
    import boto3
    from botocore.config import Config

    cfg = Config(retries={"max_attempts": 1, "mode": "standard"}, connect_timeout=10, read_timeout=30)
    kwargs: dict[str, Any] = {"region_name": region, "config": cfg}
    if creds.get("access_key_id") and creds.get("secret_access_key"):
        kwargs["aws_access_key_id"] = creds["access_key_id"]
        kwargs["aws_secret_access_key"] = creds["secret_access_key"]
        if creds.get("session_token"):
            kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client("logs", **kwargs)  # else: default chain (instance role, env, profile)


class AwsConnector(Connector):
    cloud = "aws"

    def __init__(self, config: dict, secrets: dict, client: Any = None, sleep=None):
        self.region = config.get("region") or "us-east-1"
        groups = config.get("log_groups") or config.get("log_group") or []
        self.log_groups = [groups] if isinstance(groups, str) else list(groups)
        self.filter_pattern = config.get("filter_pattern", "")
        raw = client if client is not None else make_logs_client(self.region, secrets)
        self.client = ReadOnlyClient(raw)
        self._sleep = sleep

    def _retry(self, fn):
        kw = {"sleep": self._sleep} if self._sleep else {}
        return with_retries(lambda: self._wrap(fn), **kw)

    @staticmethod
    def _wrap(fn):
        try:
            return fn()
        except ConnectorError:
            raise
        except Exception as e:  # noqa: BLE001
            raise map_aws_error(e) from e

    def test(self) -> dict[str, Any]:
        if not self.log_groups:
            raise ConnectorError("config", "No log groups configured")
        found = []
        for g in self.log_groups:
            resp = self._retry(lambda g=g: self.client.describe_log_groups(logGroupNamePrefix=g, limit=5))
            if not any(x.get("logGroupName") == g for x in resp.get("logGroups", [])):
                raise ConnectorError("notfound", f"Log group not found: {g}")
            found.append(g)
        return {"ok": True, "log_groups": found, "region": self.region}

    def fetch(self, since: datetime, until: datetime, limit: int = 2000) -> list[LogRecord]:
        if not self.log_groups:
            raise ConnectorError("config", "No log groups configured")
        out: list[LogRecord] = []
        for group in self.log_groups:
            token = None
            while len(out) < limit:
                params: dict[str, Any] = {
                    "logGroupName": group,
                    "startTime": int(since.timestamp() * 1000),
                    "endTime": int(until.timestamp() * 1000),
                    "limit": min(1000, limit - len(out)),
                }
                if self.filter_pattern:
                    params["filterPattern"] = self.filter_pattern
                if token:
                    params["nextToken"] = token
                resp = self._retry(lambda p=params: self.client.filter_log_events(**p))
                for ev in resp.get("events", []):
                    ts = datetime.fromtimestamp(ev["timestamp"] / 1000, tz=timezone.utc)
                    stream = ev.get("logStreamName", "")
                    out.append(
                        LogRecord(
                            uid=ev.get("eventId") or f"{group}:{stream}:{ev['timestamp']}",
                            ts=ts,
                            message=(ev.get("message") or "").rstrip(),
                            service=group.rsplit("/", 1)[-1] or group,
                            host=stream or None,
                            raw={"log_group": group, "log_stream": stream},
                        )
                    )
                token = resp.get("nextToken")
                if not token:
                    break
        out.sort(key=lambda r: r.ts)
        return out[:limit]
