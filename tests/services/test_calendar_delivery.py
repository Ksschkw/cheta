"""Delivery of a tool-produced calendar file, and honesty when it cannot be sent.

The tool shapes the bytes; the conversation service owns the transport. These
tests cover both halves: a real surface receives a real .ics attachment, and a
surface with no file channel is told the truth instead of being told the event
was added.
"""

from __future__ import annotations

import json

from core.tools.calendar_tools import build_calendar_tools
from core.tools.tool_registry import ToolRegistry
from tests.services.test_agent_loop import ScriptedLlm, build_harness

CALENDAR_CALL = json.dumps(
    {"title": "Dentist", "start": "2026-01-31T09:00:00Z", "location": "Lagos"}
)


class DocumentChannel:
    """A reply channel that records documents as well as messages."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.documents: list[tuple[str, str, bytes, str | None]] = []
        self.typing: list[str] = []

    async def send_message(self, recipient_id: str, text: str, reply_markup=None) -> None:
        self.sent.append((recipient_id, text))

    async def send_document(
        self, recipient_id: str, filename: str, content: bytes, caption: str | None = None
    ) -> None:
        self.documents.append((recipient_id, filename, content, caption))

    async def send_photo(self, recipient_id, image_path, caption=None, reply_markup=None) -> None:
        return None

    async def answer_callback_query(self, callback_query_id, text=None) -> None:
        return None

    async def send_typing(self, recipient_id: str) -> None:
        self.typing.append(recipient_id)


# required test 7, first half


async def test_a_calendar_event_is_delivered_as_an_ics_file_with_a_sensible_name() -> None:
    llm = ScriptedLlm(tool_name="calendar_event", arguments=CALENDAR_CALL)
    channel = DocumentChannel()
    registry = ToolRegistry(build_calendar_tools())
    _, service = build_harness(llm, registry, reply_channel=channel)

    result = await service.handle_surface_turn(
        "telegram", "42", "Ada", "put the dentist on my calendar", "42"
    )

    assert result is not None
    assert len(channel.documents) == 1
    recipient, filename, content, caption = channel.documents[0]
    assert recipient == "42"
    assert filename.endswith(".ics")
    assert b"BEGIN:VCALENDAR" in content
    assert b"SUMMARY:Dentist" in content
    assert "open" in (caption or "").lower()
    assert "calendar" in (caption or "").lower()
    # The app cannot know the calendar accepted it, so it must never say so.
    assert "added to your calendar" not in result.reply


# required test 7, second half


async def test_a_surface_without_file_delivery_says_so_plainly() -> None:
    llm = ScriptedLlm(tool_name="calendar_event", arguments=CALENDAR_CALL)
    registry = ToolRegistry(build_calendar_tools())
    _, service = build_harness(llm, registry, reply_channel=None)

    result = await service.handle_turn("web", "u1", "Ada", "put the dentist on my calendar")

    assert "cannot send files" in result.reply
    assert "Nothing was added to your calendar" in result.reply
    assert "added to your calendar" not in result.reply.replace(
        "Nothing was added to your calendar", ""
    )


# required test 6, the service-generated capability text


def test_the_generated_help_lists_the_calendar_tool() -> None:
    llm = ScriptedLlm()
    registry = ToolRegistry(build_calendar_tools())
    _, service = build_harness(llm, registry)

    text = service.command_reply("/help", "web", "u1", "Ada")

    assert "calendar_event" in text
    assert "not_a_tool" not in text
