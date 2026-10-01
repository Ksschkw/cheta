"""Persistence for the reminder entity. One entity, no joins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.database import Database
from models.entities.reminder_model import (
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    ReminderModel,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_model(row) -> ReminderModel:
    return ReminderModel(
        id=row["id"],
        user_id=row["user_id"],
        surface=row["surface"],
        recipient_id=row["recipient_id"],
        text=row["text"],
        due_at=row["due_at"],
        status=row["status"],
        created_at=row["created_at"],
        sent_at=row["sent_at"],
    )


class ReminderCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, reminder_id: str) -> ReminderModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        )
        return _row_to_model(row) if row else None

    def create(
        self,
        user_id: str,
        surface: str,
        recipient_id: str,
        text: str,
        due_at: str,
    ) -> ReminderModel:
        model = ReminderModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            surface=surface,
            recipient_id=recipient_id,
            text=text,
            due_at=due_at,
            status=STATUS_PENDING,
            created_at=_now(),
        )
        self._database.execute(
            "INSERT INTO reminders (id, user_id, surface, recipient_id, text, due_at,"
            " status, created_at, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model.id,
                model.user_id,
                model.surface,
                model.recipient_id,
                model.text,
                model.due_at,
                model.status,
                model.created_at,
                model.sent_at,
            ),
        )
        return model

    def list_for_user(self, user_id: str, limit: int = 50) -> list[ReminderModel]:
        rows = self._database.fetch_all(
            "SELECT * FROM reminders WHERE user_id = ? ORDER BY due_at ASC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_model(row) for row in rows]

    def list_pending_for_user(self, user_id: str, limit: int = 50) -> list[ReminderModel]:
        rows = self._database.fetch_all(
            "SELECT * FROM reminders WHERE user_id = ? AND status = ?"
            " ORDER BY due_at ASC LIMIT ?",
            (user_id, STATUS_PENDING, limit),
        )
        return [_row_to_model(row) for row in rows]

    def claim_due(self, due_before: str, limit: int = 20) -> list[ReminderModel]:
        """Atomically move due reminders to ``sending`` and return them.

        The move is the delivery guarantee: one row can be claimed once, so a
        scheduler tick that overlaps another cannot double-send. A delivery that
        then fails is released back to ``pending`` and retried later.
        """
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE status = ? AND due_at <= ?"
                " ORDER BY due_at ASC LIMIT ?",
                (STATUS_PENDING, due_before, limit),
            ).fetchall()
            claimed: list[ReminderModel] = []
            for row in rows:
                cursor = connection.execute(
                    "UPDATE reminders SET status = ? WHERE id = ? AND status = ?",
                    (STATUS_SENDING, row["id"], STATUS_PENDING),
                )
                if cursor.rowcount == 1:
                    claimed.append(_row_to_model(row))
            return claimed

    def mark_sent(self, reminder_id: str, sent_at: str) -> ReminderModel | None:
        changed = self._database.execute(
            "UPDATE reminders SET status = ?, sent_at = ? WHERE id = ?",
            (STATUS_SENT, sent_at, reminder_id),
        )
        return self.get_by_id(reminder_id) if changed else None

    def release(self, reminder_id: str) -> ReminderModel | None:
        """Put a failed delivery back so the next tick retries it."""
        changed = self._database.execute(
            "UPDATE reminders SET status = ? WHERE id = ? AND status = ?",
            (STATUS_PENDING, reminder_id, STATUS_SENDING),
        )
        return self.get_by_id(reminder_id) if changed else None

    def delete(self, reminder_id: str) -> bool:
        return bool(
            self._database.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        )
