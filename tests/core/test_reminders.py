"""Reminder set, list and exactly-once delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.database import Database
from core.reminder_scheduler import ReminderScheduler
from core.tools.reminder_store import ReminderStore
from core.tools.reminder_tools import build_reminder_tools
from core.tools.tool_registry import ToolContext, ToolRegistry
from crud.reminder_crud import ReminderCrud
from models.entities.reminder_model import resolve_due_at


class RecordingChannel:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_message(
        self, recipient_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.sent.append((recipient_id, text))


def build() -> tuple[ReminderCrud, ToolRegistry, ToolContext]:
    database = Database(":memory:")
    database.migrate()
    reminders = ReminderCrud(database)
    registry = ToolRegistry(build_reminder_tools(ReminderStore(reminders)))
    context = ToolContext(
        user_id="user-1",
        namespace="ns",
        surface="telegram",
        surface_user_id="42",
        recipient_id="42",
    )
    return reminders, registry, context


async def test_reminder_set_then_list_returns_it() -> None:
    _, registry, context = build()

    created = await registry.execute(
        "reminder_set", {"text": "Call mum", "when": "+30m"}, context
    )
    listed = await registry.execute("reminder_list", {}, context)

    assert created.ok is True
    assert "Saved" in created.content
    assert listed.ok is True
    assert "Call mum" in listed.content


async def test_reminder_set_refuses_an_unparseable_time() -> None:
    _, registry, context = build()

    result = await registry.execute(
        "reminder_set", {"text": "Call mum", "when": "next tuesday-ish"}, context
    )

    assert result.ok is False
    assert "when must be" in (result.error or "")


async def test_a_due_reminder_is_delivered_exactly_once() -> None:
    reminders, _, context = build()
    channel = RecordingChannel()
    scheduler = ReminderScheduler(reminders=reminders, reply_channel=channel)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    reminders.create(
        user_id=context.user_id,
        surface="telegram",
        recipient_id="42",
        text="Call mum",
        due_at=past,
    )

    first = await scheduler.deliver_due()
    second = await scheduler.deliver_due()

    assert first == 1
    assert second == 0
    assert channel.sent == [("42", "Reminder: Call mum")]


async def test_a_failed_delivery_stays_pending_and_is_retried() -> None:
    reminders, _, context = build()
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    reminders.create(
        user_id=context.user_id,
        surface="telegram",
        recipient_id="42",
        text="Call mum",
        due_at=past,
    )
    broken = ReminderScheduler(
        reminders=reminders, reply_channel=RecordingChannel(fail=True)
    )
    assert await broken.deliver_due() == 0

    working_channel = RecordingChannel()
    working = ReminderScheduler(reminders=reminders, reply_channel=working_channel)
    assert await working.deliver_due() == 1
    assert working_channel.sent == [("42", "Reminder: Call mum")]


async def test_a_reminder_with_no_push_recipient_is_not_marked_sent() -> None:
    reminders, _, context = build()
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    reminders.create(
        user_id=context.user_id,
        surface="web",
        recipient_id="",
        text="Call mum",
        due_at=past,
    )
    scheduler = ReminderScheduler(reminders=reminders, reply_channel=RecordingChannel())

    assert await scheduler.deliver_due() == 0
    pending = reminders.list_pending_for_user(context.user_id)
    assert len(pending) == 1


def test_resolve_due_at_accepts_relative_and_iso_forms() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    assert resolve_due_at("+30m", now) == now + timedelta(minutes=30)
    assert resolve_due_at("in 2 hours", now) == now + timedelta(hours=2)
    assert resolve_due_at("2026-01-02T09:00:00Z", now).hour == 9
    # A naive ISO timestamp is UTC, not local time.
    assert resolve_due_at("2026-01-02T09:00:00", now).tzinfo == UTC
    with pytest.raises(ValueError):
        resolve_due_at("sometime later", now)
