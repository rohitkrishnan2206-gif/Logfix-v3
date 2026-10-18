"""Simulated source so the MVP is demonstrable without any cloud account.
Deterministic: the same (scenario, started_at, time window) always yields the same
records, so repeated collection dedupes cleanly. Clearly labelled 'demo' in the UI."""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import Connector, LogRecord

SCENARIOS = {
    "quiet": "Healthy traffic only",
    "connection_pool": "Deploy v2.14.0 -> DB pool exhausted -> payment/gateway/order cascade",
    "oom": "Memory leak -> OOMKilled -> restart loop",
    "disk_full": "Disk fills up -> write failures",
}

_NORMAL = [
    ("api-gateway", "INFO", "GET /api/orders/{n} 200 {ms}ms"),
    ("order-service", "INFO", "Order {n} processed in {ms}ms"),
    ("payment-service", "INFO", "Payment {n} authorized"),
    ("auth-service", "INFO", "Token issued for user {n}"),
]


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha1(seed.encode()).hexdigest()[:12], 16))


def _scenario_events(scenario: str, t0: datetime, now: datetime) -> list[tuple[datetime, str, str, str]]:
    """(ts, service, level, message) events anchored to the scenario start t0."""
    ev: list[tuple[datetime, str, str, str]] = []
    mins = lambda m: t0 + timedelta(minutes=m)  # noqa: E731
    if scenario == "connection_pool":
        ev.append((mins(0), "deploy-bot", "INFO", "Deployment of payment-service v2.14.0 completed (config: db.pool.max=5)"))
        for i in range(0, 40):
            t = mins(1 + i * 0.5)
            ev.append((t, "payment-service", "ERROR",
                       f"Connection pool exhausted: timeout waiting for connection after 30000ms (active=5, max=5, waiting={10 + i})"))
            if i % 2 == 0:
                ev.append((t + timedelta(seconds=3), "api-gateway", "ERROR",
                           "Upstream payment-service timed out after 30000ms (504)"))
            if i % 3 == 0:
                ev.append((t + timedelta(seconds=6), "order-service", "ERROR",
                           f"Failed to charge order {1000 + i}: PaymentTimeout"))
        ev.append((mins(2), "payment-service", "WARN", "DB connection acquisition slow: 8200ms (threshold 1000ms)"))
    elif scenario == "oom":
        for i in range(0, 25):
            t = mins(i * 0.6)
            ev.append((t, "inventory-service", "WARN", f"Heap usage {70 + min(i, 28)}% of 2048MB, GC pause {120 + i * 40}ms"))
            if i >= 10:
                ev.append((t + timedelta(seconds=5), "inventory-service", "ERROR",
                           "java.lang.OutOfMemoryError: Java heap space"))
            if i >= 14 and i % 3 == 0:
                ev.append((t + timedelta(seconds=9), "kubelet", "CRITICAL",
                           "Container inventory-service OOMKilled (exit code 137), restarting"))
    elif scenario == "disk_full":
        for i in range(0, 20):
            t = mins(i * 0.7)
            ev.append((t, "logshipper", "WARN", f"Disk usage on /var at {90 + min(i // 2, 9)}%"))
            if i >= 8:
                ev.append((t + timedelta(seconds=4), "postgres", "ERROR",
                           "could not write to file \"pg_wal/000000010000000A\": No space left on device"))
            if i >= 12:
                ev.append((t + timedelta(seconds=8), "order-service", "ERROR",
                           "Database write failed: ENOSPC while committing transaction"))
    return [e for e in ev if e[0] <= now]


class DemoConnector(Connector):
    cloud = "demo"

    def __init__(self, config: dict, secrets: dict | None = None, now_fn=None):
        self.scenario = config.get("scenario", "quiet")
        self.started_at = config.get("scenario_started_at")
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def test(self) -> dict[str, Any]:
        return {"ok": True, "scenario": self.scenario, "note": "Simulated data, no cloud account used"}

    def fetch(self, since: datetime, until: datetime, limit: int = 2000) -> list[LogRecord]:
        until = min(until, self._now())
        out: list[LogRecord] = []
        # Healthy background traffic: ~1 record per service every 15s, deterministic per slot.
        slot = 15
        start_slot = int(since.timestamp()) // slot
        end_slot = int(until.timestamp()) // slot
        for s in range(start_slot, end_slot + 1):
            ts = datetime.fromtimestamp(s * slot, tz=timezone.utc)
            if not (since <= ts < until):
                continue
            r = _rng(f"bg{s}")
            svc, lvl, tmpl = _NORMAL[s % len(_NORMAL)]
            msg = tmpl.format(n=r.randint(1000, 9999), ms=r.randint(8, 120))
            out.append(LogRecord(uid=f"bg-{s}", ts=ts, message=f"[{svc}] {msg}", level=lvl, service=svc, host=f"{svc}-1"))
        if self.scenario != "quiet" and self.started_at:
            t0 = datetime.fromisoformat(str(self.started_at).replace("Z", "+00:00"))
            for idx, (ts, svc, lvl, msg) in enumerate(_scenario_events(self.scenario, t0, until)):
                if since <= ts < until:
                    out.append(LogRecord(uid=f"sc-{self.scenario}-{t0.timestamp():.0f}-{idx}", ts=ts,
                                         message=f"[{svc}] {msg}", level=lvl, service=svc, host=f"{svc}-1"))
        out.sort(key=lambda r: r.ts)
        return out[:limit]
