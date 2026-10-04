"""The model is told which surface it is on, in plain words.

A real user asked "What surface am I on right now?" and the assistant said it
could not tell, then apologised when told the answer. The turn already carries
the surface, so the system prompt must name it and forbid the false claim.
"""

from __future__ import annotations

import pytest

from tests.routers.test_chat_and_memory_routers import build_test_container

SURFACES = (
    ("telegram", "Telegram"),
    ("web", "the web widget"),
    ("cli", "the command line"),
    ("extension", "the browser extension"),
)


@pytest.mark.parametrize("surface,plain_name", SURFACES)
def test_the_system_prompt_names_the_surface_in_plain_words(
    surface: str, plain_name: str
) -> None:
    container, _, _ = build_test_container([])

    messages = container.conversation_service._build_prompt(
        "Ada", "what surface am I on right now?", [], surface=surface
    )
    system = messages[0].content

    assert plain_name in system
    assert f"on {plain_name}." in system
    assert "never say you cannot tell" in system.lower()


def test_an_unknown_surface_is_not_read_back_as_an_internal_value() -> None:
    container, _, _ = build_test_container([])

    messages = container.conversation_service._build_prompt(
        "Ada", "what surface am I on?", [], surface="frobnicator"
    )
    system = messages[0].content

    assert "frobnicator" not in system
    assert "on this client." in system


def test_no_self_introduction_surfaces_a_former_name() -> None:
    """The live transcript introduced itself as "Cheta (some people call me Ranti)"."""
    container, _, _ = build_test_container([])
    service = container.conversation_service

    system = service._build_prompt("Ada", "who are you?", [])[0].content
    assert "Ranti" not in system
    assert service._settings.bot_name in system

    start = service.command_reply("/start", "web", "u1", "Ada")
    assert "Ranti" not in start
    assert service._settings.bot_name in start

    onboarding = service._onboarding_text()
    assert "Ranti" not in onboarding
    assert service._settings.bot_name in onboarding

    tutorial = service.command_reply("/tutorial", "web", "u1", "Ada")
    assert "Ranti" not in tutorial
    assert service._settings.bot_name in tutorial

    help_text = service.command_reply("/help", "web", "u1", "Ada")
    assert "Ranti" not in help_text
    assert service._settings.bot_name in help_text
