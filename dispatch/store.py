"""
Durable verb-event log and circuit breaker.

v1 is 100% local/offline: an embedded libSQL (SQLite-compatible) file at
logs/agent.db. The connect() kwargs already include sync_url / auth_token /
offline, so a later Turso replica is a config change, not a rewrite.

Durability (verified against local libsql 0.1.11 on this device):
- Default journal_mode is DELETE and synchronous is FULL (2).
- We pin journal_mode=WAL (concurrent readers) and synchronous=FULL so a
  committed row survives an OS-level kill of the Termux process, not just
  a Python exception. WAL+NORMAL would only survive app crash.
- Each insert runs in BEGIN IMMEDIATE so writers take the reserved lock
  up front instead of upgrading from DEFERRED.

Do not log stdin bodies here — callers pass Verb.public_args.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import libsql

STAGES = (
    "requested",
    "approved",
    "denied",
    "timeout",
    "executed",
    "failed",
    "circuit_open",
)

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS verb_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    verb TEXT NOT NULL,
    stage TEXT NOT NULL,
    risk TEXT,
    args_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
)""",
    "CREATE INDEX IF NOT EXISTS verb_events_verb_ts ON verb_events (verb, ts DESC)",
    "CREATE INDEX IF NOT EXISTS verb_events_stage_ts ON verb_events (stage, ts DESC)",
)

# Trip if this many timeout/failed rows land inside the window.
DEFAULT_FAILURE_LIMIT = 5
DEFAULT_WINDOW_S = 60.0
DEFAULT_COOLDOWN_S = 30.0

_DEFAULT_DB = Path.home() / "agent" / "logs" / "agent.db"


class CircuitOpen(Exception):
    """Raised when the breaker is open for a verb; do not execute."""


@dataclass(frozen=True)
class StoreConfig:
    """Local file plus optional remote replica fields (unused in v1)."""

    path: Path
    sync_url: str | None = None
    auth_token: str | None = None
    offline: bool = True
    failure_limit: int = DEFAULT_FAILURE_LIMIT
    window_s: float = DEFAULT_WINDOW_S
    cooldown_s: float = DEFAULT_COOLDOWN_S

    @classmethod
    def from_env(cls, path: Path | None = None) -> "StoreConfig":
        db = path or Path(os.environ.get("AGENT_DB_PATH", str(_DEFAULT_DB)))
        sync_url = os.environ.get("LIBSQL_URL") or os.environ.get("LIBSQL_SYNC_URL") or None
        token = os.environ.get("LIBSQL_AUTH_TOKEN") or None
        offline_raw = os.environ.get("LIBSQL_OFFLINE", "1")
        offline = offline_raw not in ("0", "false", "False")
        if sync_url:
            offline = False
        return cls(path=Path(db), sync_url=sync_url, auth_token=token, offline=offline)


def _connect(cfg: StoreConfig):
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "database": str(cfg.path),
        "offline": cfg.offline,
        "isolation_level": None,  # we issue BEGIN IMMEDIATE ourselves
    }
    if cfg.sync_url:
        kwargs["sync_url"] = cfg.sync_url
        kwargs["offline"] = False
    if cfg.auth_token:
        kwargs["auth_token"] = cfg.auth_token
    return libsql.connect(**kwargs)


def _apply_durability(con) -> dict[str, Any]:
    """Pin WAL + FULL and read them back. FULL is required for OS-kill durability."""
    journal = con.execute("PRAGMA journal_mode=WAL").fetchone()
    con.execute("PRAGMA synchronous=FULL")
    sync = con.execute("PRAGMA synchronous").fetchone()
    mode = (journal[0] if journal else "").lower()
    sync_n = int(sync[0]) if sync else -1
    if mode != "wal":
        raise RuntimeError(f"expected journal_mode=WAL, got {mode!r}")
    if sync_n != 2:
        raise RuntimeError(f"expected synchronous=FULL (2), got {sync_n!r}")
    return {"journal_mode": mode, "synchronous": sync_n}


class Store:
    def __init__(self, config: StoreConfig | None = None):
        self.config = config or StoreConfig.from_env()
        self._lock = threading.Lock()
        self._con = _connect(self.config)
        self.durability = _apply_durability(self._con)
        for stmt in SCHEMA_STATEMENTS:
            self._con.execute(stmt)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def append(
        self,
        *,
        verb: str,
        stage: str,
        risk: str | None = None,
        args: dict | None = None,
        error: str | None = None,
        ts: float | None = None,
    ) -> int:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        event_ts = time.time() if ts is None else ts
        payload = json.dumps(args or {}, default=str)
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                cur = self._con.execute(
                    "INSERT INTO verb_events (ts, verb, stage, risk, args_json, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (event_ts, verb, stage, risk, payload, error),
                )
                rowid = cur.lastrowid
                self._con.commit()
            except Exception:
                self._con.rollback()
                raise
        return int(rowid)

    def recent(self, verb: str, *, stage: str | None = None, since: float | None = None, limit: int = 50) -> list[tuple]:
        sql = "SELECT id, ts, verb, stage, risk, args_json, error FROM verb_events WHERE verb = ?"
        params: list[Any] = [verb]
        if stage is not None:
            sql += " AND stage = ?"
            params.append(stage)
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return list(self._con.execute(sql, params).fetchall())

    def failure_count(self, verb: str, *, now: float | None = None) -> int:
        since = (now if now is not None else time.time()) - self.config.window_s
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) FROM verb_events WHERE verb = ? "
                "AND stage IN ('timeout', 'failed') AND ts >= ?",
                (verb, since),
            ).fetchone()
        return int(row[0]) if row else 0

    def is_open(self, verb: str, *, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        with self._lock:
            row = self._con.execute(
                "SELECT ts FROM verb_events WHERE verb = ? AND stage = 'circuit_open' "
                "ORDER BY id DESC LIMIT 1",
                (verb,),
            ).fetchone()
        if not row:
            return False
        return (t - float(row[0])) < self.config.cooldown_s

    def guard(self, verb: str, *, risk: str | None = None, args: dict | None = None) -> None:
        """Raise CircuitOpen if this verb is currently tripped."""
        if self.is_open(verb):
            self.append(verb=verb, stage="circuit_open", risk=risk, args=args)
            raise CircuitOpen(f"{verb}: circuit open")

    def record_outcome(
        self,
        *,
        verb: str,
        stage: str,
        risk: str | None = None,
        args: dict | None = None,
        error: str | None = None,
    ) -> int:
        """Insert an outcome row; trip the breaker on timeout/failed threshold."""
        rowid = self.append(verb=verb, stage=stage, risk=risk, args=args, error=error)
        if stage in ("timeout", "failed") and self.failure_count(verb) >= self.config.failure_limit:
            if not self.is_open(verb):
                self.append(verb=verb, stage="circuit_open", risk=risk, args=args, error=error)
        return rowid


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store


def reset_store(store: Store | None = None) -> None:
    """Tests: replace or drop the process singleton."""
    global _store
    with _store_lock:
        if _store is not None and store is None:
            try:
                _store.close()
            except Exception:
                pass
        _store = store
