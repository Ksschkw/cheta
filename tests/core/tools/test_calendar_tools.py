"""The calendar tool: a valid ICS document, correct value types, escaping.

No calendar library is used, here or in the tool. The point of the feature is a
plain-text standard, so the tests read the text with a tiny RFC 5545 reader and
assert on what a real calendar application would see after unfolding.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from core.config import Settings
from core.container import build_memory_gateway, build_tool_registry
from core.database import Database
from core.gateways.web_gateway import WebGateway
from core.resilience import Boundary, ResiliencePolicy
from core.tools.calendar_tools import build_calendar_tools, build_ics_event
from core.tools.reminder_store import ReminderStore
from core.tools.reminder_tools import build_reminder_tools
from core.tools.tool_registry import ToolContext, ToolRegistry
from crud.memory_crud import MemoryCrud
from crud.reminder_crud import ReminderCrud

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def parse_ics(text: str) -> dict[str, list[str]]:
    """A minimal RFC 5545 reader: unfold continuation lines, then name/value."""
    unfolded: list[str] = []
    for line in text.split("\r\n"):
        if not line:
            continue
        if line.startswith(" ") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    parsed: dict[str, list[str]] = {}
    for line in unfolded:
        name, _, value = line.partition(":")
        parsed.setdefault(name, []).append(value)
    return parsed


def telegram_context(*, can_send_documents: bool = True) -> ToolContext:
    return ToolContext(
        user_id="u1",
        namespace="ns",
        surface="telegram",
        surface_user_id="42",
        recipient_id="42",
        can_send_documents=can_send_documents,
    )


# required test 1


def test_a_timed_event_is_one_valid_vevent_with_the_expected_summary_and_start() -> None:
    ics = build_ics_event(
        title="Dentist",
        start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
        now=NOW,
        uid="uid-1",
    )

    fields = parse_ics(ics)

    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 1
    assert ics.count("END:VEVENT") == 1
    # The envelope fields a VCALENDAR must carry, and the event's own identity.
    assert fields["VERSION"] == ["2.0"]
    assert "PRODID" in fields
    assert fields["UID"] == ["uid-1"]
    assert "DTSTAMP" in fields
    assert fields["SUMMARY"] == ["Dentist"]
    assert fields["DTSTART"] == ["20260131T090000Z"]


# required test 2


def test_an_all_day_event_uses_a_date_value_not_a_date_time() -> None:
    ics = build_ics_event(
        title="Holiday",
        start=date(2026, 1, 31),
        all_day=True,
        now=NOW,
        uid="uid-2",
    )

    fields = parse_ics(ics)

    assert fields["DTSTART;VALUE=DATE"] == ["20260131"]
    # A plain DTSTART would be a DATE-TIME and would land at midnight, which is
    # the difference between the right day and the wrong one.
    assert "DTSTART" not in fields
    assert fields["DTEND;VALUE=DATE"] == ["20260201"]
    assert "T" not in fields["DTSTART;VALUE=DATE"][0]


# required test 3


def test_commas_semicolons_and_newlines_are_escaped_and_the_file_still_parses() -> None:
    title = "Dinner, with Ada; bring notes\nand a backslash \\ marker"
    ics = build_ics_event(
        title=title,
        start=datetime(2026, 1, 31, 19, 0, tzinfo=UTC),
        description="line one\nline two, with a semicolon; and a comma",
        now=NOW,
        uid="uid-3",
    )

    fields = parse_ics(ics)

    assert fields["SUMMARY"] == ["Dinner\\, with Ada\\; bring notes\\nand a backslash \\\\ marker"]
    assert fields["DESCRIPTION"] == [
        "line one\\nline two\\, with a semicolon\\; and a comma"
    ]
    # A raw newline inside a value would split the content line and the app
    # would reject the whole file, so it must not be present.
    assert "notes\nand" not in ics


# required test 4


async def test_an_unparseable_start_is_a_typed_failure_and_produces_no_file() -> None:
    spec = build_calendar_tools()[0]

    result = await spec.handler(
        {"title": "Dentist", "start": "sometime next week-ish"}, telegram_context()
    )

    assert result.ok is False
    assert result.document is None
    assert "start must be" in (result.error or "")


# required test 5


def test_an_explicit_offset_is_honoured_and_the_default_is_utc() -> None:
    explicit = build_ics_event(
        title="Call",
        start=datetime(2026, 1, 31, 9, 0, tzinfo=timezone(timedelta(hours=1))),
        now=NOW,
        uid="uid-4",
    )
    naive = build_ics_event(
        title="Call", start=datetime(2026, 1, 31, 9, 0), now=NOW, uid="uid-5"
    )

    # 09:00 at +01:00 is 08:00 UTC: the offset was honoured, not discarded.
    assert parse_ics(explicit)["DTSTART"] == ["20260131T080000Z"]
    # A naive time means UTC on this tool, and it is labelled UTC rather than
    # left as a floating local time.
    naive_value = parse_ics(naive)["DTSTART"][0]
    assert naive_value == "20260131T090000Z"
    assert naive_value.endswith("Z")


# required test 6


def test_the_calendar_tool_is_registered_and_in_the_capability_list() -> None:
    settings = Settings(database_path=":memory:")
    database = Database(":memory:")
    database.migrate()

    registry = build_tool_registry(
        memories=MemoryCrud(database),
        memory_gateway=build_memory_gateway(settings),
        reminders=ReminderCrud(database),
        web_gateway=WebGateway(
            boundary=Boundary(ResiliencePolicy(dependency="web", max_attempts=1))
        ),
    )

    definition_names = [definition.name for definition in registry.definitions()]
    assert "calendar_event" in definition_names
    assert "calendar_event" in registry.names()
    assert "calendar_event" in registry.describe()
    spec = registry.get("calendar_event")
    assert spec is not None
    # It is pure text generation, so it must never be marked as network-bound.
    assert spec.uses_network is False


# required test 8


async def test_the_reminder_tool_emits_a_calendar_file_through_the_shared_builder() -> None:
    database = Database(":memory:")
    database.migrate()
    registry = ToolRegistry(
        build_reminder_tools(ReminderStore(ReminderCrud(database)))
    )

    result = await registry.execute(
        "reminder_set",
        {"text": "Call, mum; today", "when": "+30m"},
        telegram_context(),
    )

    assert result.ok is True
    assert result.document is not None
    assert result.document.filename.endswith(".ics")
    fields = parse_ics(result.document.content.decode("utf-8"))
    # The same escaping path as the calendar tool: the reminder text is a
    # SUMMARY value and must be escaped the same way.
    assert fields["SUMMARY"] == ["Call\\, mum\\; today"]
    assert "DTSTART" in fields
