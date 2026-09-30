"""Persistence for the contradiction entity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.database import Database
from models.entities.contradiction_model import STATUS_OPEN, STATUS_RESOLVED, ContradictionModel


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_model(row) -> ContradictionModel:
    return ContradictionModel(
        id=row["id"],
        user_id=row["user_id"],
        left_blob_id=row["left_blob_id"],
        right_blob_id=row["right_blob_id"],
        reason=row["reason"],
        status=row["status"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


class ContradictionCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, contradiction_id: str) -> ContradictionModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM contradictions WHERE id = ?", (contradiction_id,)
        )
        return _row_to_model(row) if row else None

    def get_between(self, left_blob_id: str, right_blob_id: str) -> ContradictionModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM contradictions WHERE (left_blob_id = ? AND right_blob_id = ?)"
            " OR (left_blob_id = ? AND right_blob_id = ?)",
            (left_blob_id, right_blob_id, right_blob_id, left_blob_id),
        )
        return _row_to_model(row) if row else None

    def create(
        self, user_id: str, left_blob_id: str, right_blob_id: str, reason: str
    ) -> ContradictionModel:
        model = ContradictionModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            left_blob_id=left_blob_id,
            right_blob_id=right_blob_id,
            reason=reason,
            status=STATUS_OPEN,
            created_at=_now(),
            resolved_at=None,
        )
        self._database.execute(
            "INSERT INTO contradictions (id, user_id, left_blob_id, right_blob_id, reason,"
            " status, created_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model.id,
                model.user_id,
                model.left_blob_id,
                model.right_blob_id,
                model.reason,
                model.status,
                model.created_at,
                model.resolved_at,
            ),
        )
        return model

    def list_open_for_user(self, user_id: str, limit: int = 50) -> list[ContradictionModel]:
        rows = self._database.fetch_all(
            "SELECT * FROM contradictions WHERE user_id = ? AND status = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (user_id, STATUS_OPEN, limit),
        )
        return [_row_to_model(row) for row in rows]

    def resolve(self, contradiction_id: str) -> ContradictionModel | None:
        changed = self._database.execute(
            "UPDATE contradictions SET status = ?, resolved_at = ? WHERE id = ? AND status = ?",
            (STATUS_RESOLVED, _now(), contradiction_id, STATUS_OPEN),
        )
        return self.get_by_id(contradiction_id) if changed else None
