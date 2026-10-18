"""Connector contract: read-only, retried, errors classified."""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, TypeVar

T = TypeVar("T")

RETRYABLE = {"throttle", "transient"}


class ConnectorError(Exception):
    """kind: auth | notfound | throttle | transient | config | unknown"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass
class LogRecord:
    uid: str                      # unique within a source, used for dedupe
    ts: datetime                  # timezone-aware UTC
    message: str
    level: str | None = None      # cloud-native severity text if any
    service: str | None = None
    host: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def with_retries(
    fn: Callable[[], T],
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Exponential backoff with full jitter. Only throttle/transient errors retry;
    auth/config/notfound fail immediately so we never hammer a cloud with bad creds."""
    last: ConnectorError | None = None
    for i in range(attempts):
        try:
            return fn()
        except ConnectorError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                raise
            sleep(min(max_delay, base_delay * (2 ** i)) * (0.5 + rng() / 2))
    assert last is not None
    raise last


class Connector(ABC):
    cloud: str = ""

    @abstractmethod
    def fetch(self, since: datetime, until: datetime, limit: int = 2000) -> list[LogRecord]:
        """Return records with since <= ts < until, oldest first. Must be read-only."""

    @abstractmethod
    def test(self) -> dict[str, Any]:
        """Cheap connectivity/permission check. Raises ConnectorError on failure."""


def http_error(status: int, body: str = "") -> ConnectorError:
    snippet = (body or "")[:200]
    if status in (401, 403):
        return ConnectorError("auth", f"HTTP {status}: access denied. {snippet}")
    if status == 404:
        return ConnectorError("notfound", f"HTTP 404: not found. {snippet}")
    if status == 429:
        return ConnectorError("throttle", f"HTTP 429: throttled. {snippet}")
    if status >= 500 or status == 408:
        return ConnectorError("transient", f"HTTP {status}: server error. {snippet}")
    return ConnectorError("unknown", f"HTTP {status}: {snippet}")
