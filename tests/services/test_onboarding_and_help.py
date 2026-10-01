"""Onboarding, the generated /help reference, and gap-fill text."""

from __future__ import annotations

from core.tools.tool_registry import ToolRegistry
from services.conversation_service import ONBOARDING_TEXT
from tests.services.test_agent_loop import (
    ScriptedLlm,
    SpyTool,
    build_harness,
)


def registry_with(*names: str) -> ToolRegistry:
    # "spy" is always present so the scripted model's default tool call resolves.
    return ToolRegistry([SpyTool(name=name).spec() for name in (*names, "spy")])


async def test_the_first_turn_carries_onboarding_and_the_second_does_not() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))

    first = await service.handle_turn("web", "u1", "Ada", "hello")
    second = await service.handle_turn("web", "u1", "Ada", "hello again")

    assert first.first_turn is True
    assert first.onboarding_note == ONBOARDING_TEXT
    assert ONBOARDING_TEXT in first.reply
    # It says what it is, how memory works, four surfaces and what it cannot do.
    assert "Cheta" in first.reply
    assert "/memories" in first.reply
    assert "/forget" in first.reply
    assert "four surfaces" in first.reply
    assert "pairing a new client" in first.reply.lower()
    assert "cannot log into your accounts" in first.reply
    assert "cannot understand images or video" in first.reply

    assert second.first_turn is False
    assert second.onboarding_note is None
    assert ONBOARDING_TEXT not in second.reply
    assert "cannot log into your accounts" not in second.reply


def test_help_lists_every_registered_tool_and_no_unregistered_one() -> None:
    llm = ScriptedLlm()
    names = ("web_search", "crawl", "wikipedia", "weather", "calculate", "reminder_set")
    _, service = build_harness(llm, registry_with(*names))

    text = service.command_reply("/help", "web", "u1", "Ada")

    for name in names:
        assert name in text, name
    assert "not_a_tool" not in text
    # Every existing command is in the reference, including the pairing commands.
    for command in ("/start", "/help", "/memories", "/forget", "/link", "/unlink"):
        assert command in text, command


def test_help_advertises_no_tool_when_the_registry_is_empty() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, None)

    text = service.command_reply("/help", "web", "u1", "Ada")

    assert "web_search" not in text
    assert "answer from this conversation" in text


def test_the_empty_memory_reply_instructs_instead_of_only_reporting_zero() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))

    text = service.command_reply("/memories", "web", "u1", "Ada")

    assert "nothing stored about you yet" in text
    # It says how a note is created, not only that there are none.
    assert "how a note gets created" in text
    assert "durable" in text


def test_a_degraded_memory_read_must_not_be_reported_as_no_memory() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))

    system = service._build_prompt(
        "Ada", "hello", [], 0, tool_summary="", memory_degraded=True
    )[0].content

    assert "briefly unreachable" in system
    assert "Do not say that you have no memory" in system
    # It must not fall into the "nothing stored" branch when the read failed.
    assert "nothing stored about this person yet" not in system
