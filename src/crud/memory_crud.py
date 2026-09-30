"""Persistence for the memory entity. One entity, no joins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.database import Database
from models.entities.memory_model import STATUS_ACTIVE, MemoryModel


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_model(row) -> MemoryModel:
    return MemoryModel(
        id=row["id"],
        user_id=row["user_id"],
        blob_id=row["blob_id"],
        namespace=row["namespace"],
        text=row["text"],
        importance=float(row["importance"]),
        origin_surface=row["origin_surface"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
    )


class MemoryCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, memory_id: str) -> MemoryModel | None:
        row = self._database.fetch_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return _row_to_model(row) if row else None

    def get_by_blob_id(self, blob_id: str) -> MemoryModel | None:
        row = self._database.fetch_one("SELECT * FROM memories WHERE blob_id = ?", (blob_id,))
        return _row_to_model(row) if row else None

    def list_for_user(
        self, user_id: str, status: str | None = None, limit: int = 200
    ) -> list[MemoryModel]:
        if status is None:
            rows = self._database.fetch_all(
                "SELECT * FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            rows = self._database.fetch_all(
                "SELECT * FROM memories WHERE user_id = ? AND status = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (user_id, status, limit),
            )
        return [_row_to_model(row) for row in rows]

    def count_for_user(self, user_id: str, status: str | None = STATUS_ACTIVE) -> int:
        if status is None:
            row = self._database.fetch_one(
                "SELECT COUNT(*) AS total FROM memories WHERE user_id = ?", (user_id,)
            )
        else:
            row = self._database.fetch_one(
                "SELECT COUNT(*) AS total FROM memories WHERE user_id = ? AND status = ?",
                (user_id, status),
            )
        return int(row["total"]) if row else 0

    def create(
        self,
        user_id: str,
        blob_id: str,
        namespace: str,
        text: str,
        importance: float,
        origin_surface: str,
        occurred_at: str,
    ) -> MemoryModel:
        model = MemoryModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            blob_id=blob_id,
            namespace=namespace,
            text=text,
            importance=importance,
            origin_surface=origin_surface,
            status=STATUS_ACTIVE,
            superseded_by=None,
            occurred_at=occurred_at,
            created_at=_now(),
        )
        self._database.execute(
            "INSERT INTO memories (id, user_id, blob_id, namespace, text, importance,"
            " origin_surface, status, superseded_by, occurred_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model.id,
                model.user_id,
                model.blob_id,
                model.namespace,
                model.text,
                model.importance,
                model.origin_surface,
                model.status,
                model.superseded_by,
                model.occurred_at,
                model.created_at,
            ),
        )
        return model

    def mark_status(
        self, memory_id: str, status: str, superseded_by: str | None = None
    ) -> MemoryModel | None:
        changed = self._database.execute(
            "UPDATE memories SET status = ?, superseded_by = ? WHERE id = ?",
            (status, superseded_by, memory_id),
        )
        return self.get_by_id(memory_id) if changed else None

    def delete_by_blob_id(self, blob_id: str) -> bool:
        return bool(
            self._database.execute("DELETE FROM memories WHERE blob_id = ?", (blob_id,))
        )
