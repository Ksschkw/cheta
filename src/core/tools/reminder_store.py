"""A thin store adapter over the reminder repository, injected into the tools.

The tool handlers shape text; this holds the reminder rows. Keeping it separate
means the reminder tool can be tested against an in-memory database and the
scheduler shares exactly the same store.
"""

from __future__ import annotations

from typing import Protocol

from crud.reminder_crud import ReminderCrud
from models.entities.reminder_model import ReminderModel


class ReminderStoreProtocol(Protocol):
    def create(
        self, user_id: str, surface: str, recipient_id: str, text: str, due_at: str
    ) -> ReminderModel: ...

    def list_pending(self, user_id: str, limit: int = 20) -> list[ReminderModel]: ...


class ReminderStore:
    def __init__(self, reminders: ReminderCrud) -> None:
        self._reminders = reminders

    def create(
        self, user_id: str, surface: str, recipient_id: str, text: str, due_at: str
    ) -> ReminderModel:
        return self._reminders.create(
            user_id=user_id,
            surface=surface,
            recipient_id=recipient_id,
            text=text,
            due_at=due_at,
        )

    def list_pending(self, user_id: str, limit: int = 20) -> list[ReminderModel]:
        return self._reminders.list_pending_for_user(user_id, limit)
