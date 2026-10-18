"""SQLite persistence. One short-lived connection per call keeps it thread-safe and
simple. SQLite is fine for a single-instance MVP; swap this module for Postgres
before running more than one replica."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','analyst','viewer')),
  disabled INTEGER NOT NULL DEFAULT 0,
  must_change INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  cloud TEXT NOT NULL CHECK(cloud IN ('aws','azure','gcp','demo')),
  config TEXT NOT NULL DEFAULT '{}',
  secrets_enc TEXT,
  interval_seconds INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_collected_at TEXT,
  last_status TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  uid TEXT NOT NULL,
  ts TEXT NOT NULL,
  severity TEXT NOT NULL,
  service TEXT,
  host TEXT,
  message TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  raw TEXT,
  UNIQUE(source_id, uid)
);
CREATE INDEX IF NOT EXISTS ix_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS ix_logs_src_ts ON logs(source_id, ts);
CREATE INDEX IF NOT EXISTS ix_logs_fp ON logs(fingerprint);
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved')),
  severity TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  service TEXT,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  event_count INTEGER NOT NULL DEFAULT 0,
  sample_log_id INTEGER,
  summary TEXT,
  resolution_notes TEXT,
  auto_investigated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inc_fp ON incidents(fingerprint, status);
CREATE TABLE IF NOT EXISTS investigations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
  question TEXT,
  status TEXT NOT NULL,
  result TEXT,
  trace TEXT,
  provider TEXT,
  model TEXT,
  error TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_docs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source TEXT,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES kb_docs(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  user_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  trace TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  root_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  old_text TEXT NOT NULL,
  new_text TEXT NOT NULL,
  rationale TEXT,
  diff TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected','stale')),
  created_by TEXT,
  created_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  trigger TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_inc ON pipeline_runs(incident_id, id);
CREATE TABLE IF NOT EXISTS agent_activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  run_id INTEGER,
  incident_id INTEGER,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ms INTEGER,
  summary TEXT,
  result TEXT,
  error TEXT,
  provider TEXT,
  model TEXT,
  created_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_act_agent ON agent_activity(agent, id);
CREATE INDEX IF NOT EXISTS ix_act_run ON agent_activity(run_id);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
  run_id INTEGER,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  pr_number INTEGER NOT NULL,
  title TEXT,
  head_sha TEXT,
  author TEXT,
  status TEXT NOT NULL,
  result TEXT,
  trace TEXT,
  comment_url TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS webhook_deliveries(
  delivery_id TEXT PRIMARY KEY,
  repo_id INTEGER,
  event TEXT,
  received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Fixed-width UTC ISO string so lexicographic order == chronological order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_iso() -> str:
    return to_iso(utcnow())


def parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Columns added after the first release. CREATE TABLE IF NOT EXISTS never alters an existing
# table, so older databases (e.g. a persistent disk) are upgraded here, idempotently.
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "incidents": [
        ("priority", "TEXT"), ("score", "INTEGER"), ("category", "TEXT"), ("duplicate_of", "INTEGER"),
        ("resolved_at", "TEXT"), ("eta_min", "INTEGER"), ("eta_max", "INTEGER"), ("itsm_ref", "TEXT"), ("itsm_url", "TEXT"),
    ],
    "logs": [("redactions", "INTEGER NOT NULL DEFAULT 0")],
    "repos": [
        ("kind", "TEXT NOT NULL DEFAULT 'local'"), ("url", "TEXT"), ("branch", "TEXT"), ("token_enc", "TEXT"),
        ("webhook_secret_enc", "TEXT"), ("last_synced_at", "TEXT"), ("last_commit", "TEXT"), ("sync_status", "TEXT"),
        ("sync_error", "TEXT"), ("pr_comments", "INTEGER NOT NULL DEFAULT 0"), ("allow_pr", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "proposals": [("pr_url", "TEXT")],
}


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
            for table, cols in MIGRATIONS.items():
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
                for name, decl in cols:
                    if name not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self.connect() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid or 0

    # ---- small helpers used everywhere ----
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def audit(self, actor: str | None, action: str, target: str | None = None, detail: Any = None) -> None:
        self.execute(
            "INSERT INTO audit(ts,actor,action,target,detail) VALUES(?,?,?,?,?)",
            (now_iso(), actor, action, target, json.dumps(detail) if detail is not None else None),
        )
