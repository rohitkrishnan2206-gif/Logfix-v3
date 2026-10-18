"""Shared application state passed to API routes, agents and the scheduler."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings
from .db import Database
from .security import LoginLimiter, SecretBox


@dataclass
class AppContext:
    settings: Settings
    db: Database
    secrets: SecretBox
    jwt_secret: str
    limiter: LoginLimiter = field(default_factory=LoginLimiter)
    kb: Any = None                      # kb.store.KnowledgeBase
    llm: Any = None                     # llm.factory.LLMManager
    scheduler: Any = None               # services.scheduler.Scheduler
    connector_factory: Callable[..., Any] | None = None  # (source_row, secrets) -> Connector
    # Limit concurrent LLM investigations so a burst of incidents can't run up a bill.
    investigation_slots: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(2))
