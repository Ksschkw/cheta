"""Onboarding, the generated /help reference, and gap-fill text."""

from __future__ import annotations

from core.tools.tool_registry import ToolRegistry
from services.conversation_service import ONBOARDING_TEXT, onboarding_text
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


# ---------------------------------------------------- one newness predicate


def _seed_notes(service, user, count: int) -> None:
    namespace = service._settings.memory_namespace(user.memory_key)
    for index in range(count):
        service._memories.create(
            user_id=user.id,
            blob_id=f"blob-{index}",
            namespace=namespace,
            text=f"Preference number {index}",
            importance=0.5,
            origin_surface="telegram",
            occurred_at="2026-01-01T00:00:00+00:00",
        )


async def test_a_person_with_stored_memories_but_no_turns_is_not_new() -> None:
    """The exact shape a redeploy leaves: notes in Walrus, empty turns table."""
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))
    user = service._users.get_or_create("telegram", "42", "Ada")
    _seed_notes(service, user, 20)
    assert service._turns.count_for_user(user.id) == 0

    result = await service.handle_turn("telegram", "42", "Ada", "hello")

    assert result.first_turn is False
    assert result.onboarding_note is None
    assert ONBOARDING_TEXT not in result.reply
    assert "You are new here" not in result.reply


async def test_a_person_with_nothing_anywhere_is_new_and_gets_onboarding() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))
    user = service._users.get_or_create("web", "nobody", "Ada")

    assert service.is_new_person(user) is True
    result = await service.handle_turn("web", "nobody", "Ada", "hello")

    assert result.first_turn is True
    assert result.onboarding_note is not None
    assert "You are new here" in result.reply


async def test_a_person_with_a_shared_handle_and_an_empty_database_is_not_new() -> None:
    """A shared handle is history even when nothing local exists yet."""
    llm = ScriptedLlm()
    _, service = build_harness(llm, registry_with("calculate"))
    user = service._users.get_or_create("telegram", "42", "Ada")
    service._users.set_memory_handle(user.id, "shared-space")
    assert service._turns.count_for_user(user.id) == 0
    assert service._scope_count(user.id, None) == 0

    result = await service.handle_turn("telegram", "42", "Ada", "hello")

    assert result.first_turn is False
    assert result.onboarding_note is None


def test_onboarding_uses_the_one_configured_bot_name() -> None:
    text = onboarding_text("Aria")

    assert "I am Aria" in text
    assert "Ranti" not in text
    assert ONBOARDING_TEXT == onboarding_text("Cheta")
