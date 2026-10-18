"""In-process scheduler: a single daemon thread ticks every few seconds and collects
any source whose interval has elapsed. Fine for one instance; use a real job runner
(or one leader replica) if you scale out."""
from __future__ import annotations

import threading
import time
from datetime import timedelta

from ..context import AppContext
from ..db import parse_ts, utcnow
from .collector import collect_source, purge_old_logs


class Scheduler:
    def __init__(self, actx: AppContext):
        self.actx = actx
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[int] = set()
        self._lock = threading.Lock()
        self._last_purge = 0.0
        self.last_tick: str | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="logfix-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _loop(self) -> None:
        tick = self.actx.settings.scheduler_tick_seconds
        while not self._stop.wait(tick):
            try:
                self.tick()
            except Exception:  # noqa: BLE001  never let the loop die
                pass

    def tick(self) -> list[int]:
        """Public so tests can drive it without waiting. Returns source ids started."""
        from ..db import now_iso
        self.last_tick = now_iso()
        started = []
        for s in self.actx.db.query("SELECT id,interval_seconds,last_collected_at FROM sources WHERE enabled=1 AND interval_seconds>0"):
            last = parse_ts(s["last_collected_at"])
            if last and utcnow() - last < timedelta(seconds=s["interval_seconds"]):
                continue
            with self._lock:
                if s["id"] in self._running:
                    continue
                self._running.add(s["id"])
            started.append(s["id"])
            threading.Thread(target=self._run, args=(s["id"],), daemon=True).start()
        if time.time() - self._last_purge > 3600:
            self._last_purge = time.time()
            purge_old_logs(self.actx)
        return started

    def _run(self, source_id: int) -> None:
        try:
            collect_source(self.actx, source_id)
        finally:
            with self._lock:
                self._running.discard(source_id)
