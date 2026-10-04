"""Durable claiming and pruning for Telegram webhook updates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.database import Database
from core.telegram_dispatch import TelegramUpdateRunner
from crud.seen_update_crud import SeenUpdateCrud


def test_a_claim_is_recorded_once_and_pruned_when_old() -> None:
    database = Database(":memory:")
    database.migrate()
    clock = {"now": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)}
    runner = TelegramUpdateRunner(
        seen_updates=SeenUpdateCrud(database),
        retention_seconds=60.0,
        now=lambda: clock["now"],
    )

    assert runner.claim("1") is True
    assert runner.claim("1") is False

    clock["now"] = clock["now"] + timedelta(seconds=61)
    assert runner.claim("2") is True

    seen = SeenUpdateCrud(database)
    assert seen.is_seen("1") is False
    assert seen.is_seen("2") is True
