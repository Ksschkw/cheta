"""SQLite lifecycle and schema. Raw SQL stays inside this module and ``crud``."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        surface TEXT NOT NULL,
        surface_user_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (surface, surface_user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS surfaces (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        surface TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        UNIQUE (user_id, surface)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        blob_id TEXT NOT NULL,
        namespace TEXT NOT NULL,
        text TEXT NOT NULL,
        importance REAL NOT NULL,
        origin_surface TEXT NOT NULL,
        status TEXT NOT NULL,
        superseded_by TEXT,
        occurred_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (blob_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_links (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        source_blob_id TEXT NOT NULL,
        target_blob_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        confidence REAL NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (source_blob_id, target_blob_id, relation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        surface TEXT NOT NULL,
        user_text TEXT NOT NULL,
        assistant_text TEXT NOT NULL,
        recalled_blob_ids TEXT NOT NULL,
        memory_enabled INTEGER NOT NULL,
        counterfactual_text TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contradictions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        left_blob_id TEXT NOT NULL,
        right_blob_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        UNIQUE (left_blob_id, right_blob_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS passports (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        namespace TEXT NOT NULL,
        memory_count INTEGER NOT NULL,
        payload_sha256 TEXT NOT NULL,
        index_blob_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
)

INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories (user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_turns_user ON turns (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_contradictions_user ON contradictions (user_id, status)",
)


class Database:
    """A single guarded SQLite connection. Injected into crud, never into services."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    @property
    def path(self) -> str:
        return self._path

    def _ensure_open(self) -> sqlite3.Connection:
        if self._connection is None:
            if self._path != ":memory:":
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._connection = connection
        return self._connection

    def migrate(self) -> None:
        with self._lock:
            connection = self._ensure_open()
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            for statement in INDEX_STATEMENTS:
                connection.execute(statement)
            connection.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            connection = self._ensure_open()
            cursor = connection.execute(sql, tuple(params))
            connection.commit()
            return cursor.rowcount

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            connection = self._ensure_open()
            return list(connection.execute(sql, tuple(params)).fetchall())

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            connection = self._ensure_open()
            return connection.execute(sql, tuple(params)).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._ensure_open()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
