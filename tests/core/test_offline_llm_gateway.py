"""The zero-credential path must actually work, not merely be documented."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.container import build_llm_gateway
from core.gateways.llm_gateway import LlmGateway
from core.gateways.offline_llm_gateway import (
    ADJUDICATION_MARKER,
    EXTRACTION_MARKER,
    OfflineLlm,
    adjudicate,
    extract_facts,
)
from main import create_app
from schemas.llm_schema import ChatMessageSchema
from services.conversation_service import ADJUDICATION_PROMPT, EXTRACTION_PROMPT


def test_offline_markers_still_match_the_real_prompts() -> None:
    """Guards the coupling: if the prompts change, this fails loudly."""
    assert EXTRACTION_MARKER in EXTRACTION_PROMPT
    assert ADJUDICATION_MARKER in ADJUDICATION_PROMPT


def test_a_fresh_environment_selects_the_offline_model_not_a_dead_daemon() -> None:
    """Regression guard: Ollama used to be auto-selected and every turn failed."""
    settings = Settings.from_env({})

    assert settings.llm_providers == ()
    assert isinstance(build_llm_gateway(settings), OfflineLlm)


def test_ollama_is_only_used_when_explicitly_enabled() -> None:
    disabled = Settings.from_env({"OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1"})
    assert disabled.llm_providers == ()

    enabled = Settings.from_env(
        {"OLLAMA_ENABLED": "1", "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1"}
    )
    assert [provider.name for provider in enabled.llm_providers] == ["ollama"]


def test_a_hosted_key_always_wins_over_the_offline_model() -> None:
    settings = Settings.from_env({"GROQ_API_KEY": "test-key"})

    assert [provider.name for provider in settings.llm_providers] == ["groq"]
    assert isinstance(build_llm_gateway(settings), LlmGateway)


def test_extraction_pulls_durable_first_person_facts_only() -> None:
    facts = extract_facts(
        "User said: I am allergic to peanuts and I prefer Python. Nice weather today."
        "\nAssistant replied: noted"
    )

    texts = [str(fact["text"]) for fact in facts]
    assert len(texts) >= 2
    assert any("allergic to peanuts" in text for text in texts)
    assert any("prefers Python" in text for text in texts)
    assert all(" and I " not in text for text in texts)
    assert all("weather" not in text for text in texts)


def test_high_stakes_facts_outrank_chatter() -> None:
    facts = extract_facts("User said: I am allergic to peanuts. I like tea.\nAssistant replied: ok")
    by_text = {str(fact["text"]): fact["importance"] for fact in facts}

    assert max(by_text.values()) == 1.0
    assert any(value == 1.0 for value in by_text.values())


def test_adjudication_separates_the_four_verdicts() -> None:
    assert adjudicate("The user is allergic to peanuts", "The user is allergic to peanuts") == "SAME"
    assert adjudicate("The user eats meat", "The user does not eat meat") == "CONTRADICTS"
    assert (
        adjudicate("The user works as a backend engineer", "The user works as a platform engineer")
        == "UPDATES"
    )
    assert adjudicate("The user lives in Lagos", "The user plays the trumpet") == "DIFFERENT"


@pytest.mark.parametrize(
    ("existing", "candidate", "expected"),
    [
        ("The user is allergic to peanuts", "The user is allergic to peanuts", "SAME"),
        ("The user eats meat", "The user does not eat meat", "CONTRADICTS"),
        (
            "The user takes coffee with oat milk",
            "The user does not take coffee with oat milk",
            "CONTRADICTS",
        ),
        ("The user prefers TypeScript", "The user prefers Python", "UPDATES"),
        (
            "The user works as a backend engineer",
            "The user works as a platform engineer",
            "UPDATES",
        ),
        ("The user lives in Lagos", "The user plays the trumpet", "DIFFERENT"),
        ("The user is allergic to peanuts", "The user prefers Python", "DIFFERENT"),
    ],
)
def test_a_value_swap_is_an_update_not_a_new_fact(
    existing: str, candidate: str, expected: str
) -> None:
    """Bag-of-words overlap cannot see that only the object changed."""
    assert adjudicate(existing, candidate) == expected


async def test_offline_model_answers_all_three_prompt_shapes() -> None:
    model = OfflineLlm()

    extracted = await model.complete(
        [
            ChatMessageSchema(role="system", content=EXTRACTION_PROMPT),
            ChatMessageSchema(role="user", content="I prefer Python"),
        ]
    )
    assert extracted.provider == "offline"
    assert "prefers Python" in extracted.text

    verdict = await model.complete(
        [
            ChatMessageSchema(role="system", content=ADJUDICATION_PROMPT),
            ChatMessageSchema(
                role="user",
                content=(
                    "REMEMBERED: The user is allergic to peanuts\n"
                    "CANDIDATE: The user is allergic to peanuts"
                ),
            ),
        ]
    )
    assert verdict.text == "SAME"

    reply = await model.complete(
        [
            ChatMessageSchema(role="system", content="You are Ranti. Nothing stored."),
            ChatMessageSchema(role="user", content="hello"),
        ]
    )
    assert "do not have any memories" in reply.text


def test_a_clean_clone_with_no_credentials_completes_a_full_memory_cycle() -> None:
    """The README claims this works. This test is that claim, executable."""
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="ranti")
    with TestClient(create_app(settings=settings)) as client:
        health = client.get("/health").json()
        assert health["memory"]["mode"] == "mock"
        assert health["llm"]["providers"] == ["offline"]

        first = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "clean-clone",
                "display_name": "Ada",
                "text": "I am allergic to peanuts and I prefer Python.",
            },
        )
        assert first.status_code == 200, first.text
        learned = first.json()
        assert len(learned["stored_facts"]) >= 2
        assert learned["memory_degraded"] is False

        second = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "clean-clone",
                "display_name": "Ada",
                "text": "I am allergic to peanuts",
            },
        )
        assert second.status_code == 200, second.text
        recalled = second.json()["recalled"]

    assert recalled, "the offline path failed to recall what it had just stored"
    assert any("peanuts" in memory["text"] for memory in recalled)
    assert second.json()["skipped_duplicates"] >= 1
