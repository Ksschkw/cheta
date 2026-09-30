"""Persistence for the turn entity."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from core.database import Database
from models.entities.turn_model import TurnModel


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_model(row) -> TurnModel:
    return TurnModel(
        id=row["id"],
        user_id=row["user_id"],
        surface=row["surface"],
        user_text=row["user_text"],
        assistant_text=row["assistant_text"],
        recalled_blob_ids=tuple(json.loads(row["recalled_blob_ids"])),
        memory_enabled=bool(row["memory_enabled"]),
        counterfactual_text=row["counterfactual_text"],
        created_at=row["created_at"],
    )


class TurnCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, turn_id: str) -> TurnModel | None:
        row = self._database.fetch_one("SELECT * FROM turns WHERE id = ?", (turn_id,))
        return _row_to_model(row) if row else None

    def create(
        self,
        user_id: str,
        surface: str,
        user_text: str,
        assistant_text: str,
        recalled_blob_ids: tuple[str, ...],
        memory_enabled: bool,
        counterfactual_text: str | None = None,
    ) -> TurnModel:
        model = TurnModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            surface=surface,
            user_text=user_text,
            assistant_text=assistant_text,
            recalled_blob_ids=recalled_blob_ids,
            memory_enabled=memory_enabled,
            counterfactual_text=counterfactual_text,
            created_at=_now(),
        )
        self._database.execute(
            "INSERT INTO turns (id, user_id, surface, user_text, assistant_text,"
            " recalled_blob_ids, memory_enabled, counterfactual_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model.id,
                model.user_id,
                model.surface,
                model.user_text,
                model.assistant_text,
                json.dumps(list(model.recalled_blob_ids)),
                int(model.memory_enabled),
                model.counterfactual_text,
                model.created_at,
            ),
        )
        return model

    def attach_counterfactual(self, turn_id: str, counterfactual_text: str) -> TurnModel | None:
        changed = self._database.execute(
            "UPDATE turns SET counterfactual_text = ? WHERE id = ?",
            (counterfactual_text, turn_id),
        )
        return self.get_by_id(turn_id) if changed else None

    def list_for_user(self, user_id: str, limit: int = 50) -> list[TurnModel]:
        rows = self._database.fetch_all(
            "SELECT * FROM turns WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_model(row) for row in rows]

    def count_for_user(self, user_id: str) -> int:
        row = self._database.fetch_one(
            "SELECT COUNT(*) AS total FROM turns WHERE user_id = ?", (user_id,)
        )
        return int(row["total"]) if row else 0
