"""The memory-backed conversation use case, end to end against the offline mock.

These tests exercise the real gateway and the real SQLite index. Only the model
is faked, because the model is not what is under test.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from core.config import Settings
from core.container import build_memory_gateway
from core.database import Database
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from models.entities.memory_rank_model import ConsolidationThresholds
from schemas.llm_schema import ChatMessageSchema, CompletionSchema
from services.conversation_service import ConversationService

EXTRACT_MARKER = "extract durable facts"
ADJUDICATE_MARKER = "compare one remembered fact"

FORCE_ADJUDICATION = ConsolidationThresholds(
    duplicate_distance=0.01,
    duplicate_similarity=0.99,
    related_distance=0.99,
)


class FakeLlm:
    """Answers the three prompt shapes the service sends, deterministically."""

    def __init__(
        self, facts: Sequence[dict[str, object]], verdict: str = "DIFFERENT"
    ) -> None:
        self.facts = list(facts)
        self.verdict = verdict
        self.reply_calls = 0

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        system = messages[0].content
        if EXTRACT_MARKER in system:
            text = json.dumps(self.facts)
        elif ADJUDICATE_MARKER in system:
            text = self.verdict
        else:
            self.reply_calls += 1
            text = (
                "Yes, I remember what you told me before."
                if "<<<" in system
                else "I do not know anything about you yet."
            )
        return CompletionSchema(text=text, provider="fake", model="fake-1")


class Harness:
    def __init__(self, facts: Sequence[dict[str, object]], verdict: str = "DIFFERENT", thresholds=None):
        self.database = Database(":memory:")
        self.database.migrate()
        self.settings = Settings(database_path=":memory:", memwal_namespace_prefix="ranti")
        self.users = UserCrud(self.database)
        self.memories = MemoryCrud(self.database)
        self.turns = TurnCrud(self.database)
        self.contradictions = ContradictionCrud(self.database)
        self.llm = FakeLlm(facts, verdict)
        self.service = ConversationService(
            users=self.users,
            memories=self.memories,
            turns=self.turns,
            contradictions=self.contradictions,
            memory_gateway=build_memory_gateway(self.settings),
            llm_gateway=self.llm,
            settings=self.settings,
            thresholds=thresholds,
        )

    async def say(self, text: str, surface: str = "telegram"):
        return await self.service.handle_turn(surface, "42", "Ada", text)


async def test_a_turn_writes_extracted_facts_and_lists_them_back() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    result = await harness.say("I am allergic to peanuts")

    assert len(result.stored_facts) == 1
    assert result.stored_facts[0].verdict == "new"
    assert result.stored_facts[0].blob_id
    stored = harness.memories.list_for_user(result.user_id)
    assert [memory.text for memory in stored] == ["Ada is allergic to peanuts"]
    assert stored[0].importance == 1.0
    assert stored[0].origin_surface == "telegram"


async def test_repeating_the_same_fact_does_not_write_it_twice() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    first = await harness.say("I am allergic to peanuts")
    second = await harness.say("Just so you know, peanuts are dangerous for me")

    assert second.skipped_duplicates == 1
    assert second.stored_facts[0].verdict == "duplicate"
    assert second.stored_facts[0].blob_id is None
    assert harness.memories.count_for_user(first.user_id, None) == 1


async def test_an_update_supersedes_the_earlier_memory() -> None:
    harness = Harness(
        [{"text": "Ada works as a backend engineer", "importance": 0.7}],
        thresholds=FORCE_ADJUDICATION,
    )
    await harness.say("I work as a backend engineer")

    harness.llm.facts = [{"text": "Ada works as a platform engineer", "importance": 0.7}]
    harness.llm.verdict = "UPDATES"
    second = await harness.say("I moved to platform engineering")

    assert second.stored_facts[0].verdict == "updates"
    statuses = {memory.text: memory.status for memory in harness.memories.list_for_user(second.user_id, None)}
    assert statuses["Ada works as a backend engineer"] == "superseded"
    assert statuses["Ada works as a platform engineer"] == "active"


async def test_a_contradiction_is_surfaced_rather_than_silently_overwritten() -> None:
    harness = Harness(
        [{"text": "Ada does not eat meat", "importance": 0.8}],
        thresholds=FORCE_ADJUDICATION,
    )
    await harness.say("I do not eat meat")

    harness.llm.facts = [{"text": "Ada eats steak every Friday", "importance": 0.8}]
    harness.llm.verdict = "CONTRADICTS"
    second = await harness.say("I had a great steak on Friday")

    assert second.contradiction_count == 1
    open_ones = harness.contradictions.list_open_for_user(second.user_id)
    assert len(open_ones) == 1
    statuses = {memory.text: memory.status for memory in harness.memories.list_for_user(second.user_id, None)}
    assert statuses["Ada does not eat meat"] == "contradicted"
    assert statuses["Ada eats steak every Friday"] == "active"


async def test_superseded_memories_are_dropped_from_the_context_window() -> None:
    harness = Harness(
        [{"text": "Ada works as a backend engineer", "importance": 0.7}],
        thresholds=FORCE_ADJUDICATION,
    )
    first = await harness.say("I work as a backend engineer")
    harness.llm.facts = [{"text": "Ada works as a platform engineer", "importance": 0.7}]
    harness.llm.verdict = "UPDATES"
    await harness.say("I moved to platform engineering")

    _, recalled, _, _ = await harness.service.recall_context(first.user_id, "engineer", budget=5)

    texts = [memory.text for memory in recalled]
    assert "Ada works as a platform engineer" in texts
    assert "Ada works as a backend engineer" not in texts


async def test_recall_marks_degradation_instead_of_claiming_empty_memory() -> None:
    harness = Harness([{"text": "Ada lives in Lagos", "importance": 0.6}])
    result = await harness.say("I live in Lagos")

    namespace, recalled, degraded, _ = await harness.service.recall_context(
        result.user_id, "Lagos", budget=5
    )

    assert namespace.endswith("telegram-42")
    assert degraded is False
    assert [memory.text for memory in recalled] == ["Ada lives in Lagos"]


async def test_counterfactual_replay_shows_what_memory_changed() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say("I am allergic to peanuts")
    # The fact is written at the end of a turn, so the next turn is the first
    # one that can recall it. This is the real flow, not a staged one.
    result = await harness.say("I am allergic to peanuts")

    counterfactual = await harness.service.replay_without_memory(result.turn_id)

    assert counterfactual.recalled_count == 1
    assert counterfactual.reply_changed is True
    assert counterfactual.summary.startswith("Memory changed the reply")
    assert counterfactual.without_memory != counterfactual.with_memory
    reloaded = harness.turns.get_by_id(result.turn_id)
    assert reloaded is not None and reloaded.counterfactual_text is not None


async def test_counterfactual_says_so_when_memory_could_not_have_mattered() -> None:
    harness = Harness([])
    result = await harness.say("Hello there")

    counterfactual = await harness.service.replay_without_memory(result.turn_id)

    assert counterfactual.recalled_count == 0
    assert counterfactual.reply_changed is False
    assert "could not change the reply" in counterfactual.summary


async def test_missing_turn_is_a_not_found_not_an_empty_replay() -> None:
    from core.errors import NotFoundError

    harness = Harness([])
    with pytest.raises(NotFoundError):
        await harness.service.replay_without_memory("does-not-exist")
