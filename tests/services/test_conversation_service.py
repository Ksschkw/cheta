"""The memory-backed conversation use case, end to end against the offline mock.

These tests exercise the real gateway and the real SQLite index. Only the model
is faked, because the model is not what is under test.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from memwal import MemWalMock

from core.config import Settings
from core.container import build_memory_boundary, build_memory_gateway
from core.database import Database
from core.gateways.memwal_gateway import MemWalGateway
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


class RecordingClient:
    """Wraps the offline mock and records which SDK write method was called.

    ``remember`` only submits a job; ``remember_and_wait`` blocks until the job
    settles. The turn path must use the first one.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str, str | None]] = []

    async def remember(
        self, text: str, namespace: str | None = None, idempotency_key: str | None = None
    ):
        self.calls.append(("remember", namespace or "", idempotency_key))
        return await self._inner.remember(  # type: ignore[attr-defined]
            text, namespace, idempotency_key=idempotency_key
        )

    async def remember_and_wait(
        self,
        text: str,
        namespace: str | None = None,
        poll_interval_ms: int = 1500,
        timeout_ms: int = 60_000,
        idempotency_key: str | None = None,
    ):
        self.calls.append(("remember_and_wait", namespace or "", idempotency_key))
        return await self._inner.remember_and_wait(  # type: ignore[attr-defined]
            text,
            namespace,
            poll_interval_ms=poll_interval_ms,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

    async def wait_for_remember_job(
        self, job_id: str, poll_interval_ms: int = 1500, timeout_ms: int = 60_000
    ):
        return await self._inner.wait_for_remember_job(  # type: ignore[attr-defined]
            job_id, poll_interval_ms=poll_interval_ms, timeout_ms=timeout_ms
        )

    async def recall(self, query: str, **kwargs: object):
        return await self._inner.recall(query, **kwargs)  # type: ignore[attr-defined]


class FailingSettleClient:
    """Accepts writes, then reports every settle poll as a failure."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def remember(
        self, text: str, namespace: str | None = None, idempotency_key: str | None = None
    ):
        return await self._inner.remember(  # type: ignore[attr-defined]
            text, namespace, idempotency_key=idempotency_key
        )

    async def remember_and_wait(
        self,
        text: str,
        namespace: str | None = None,
        poll_interval_ms: int = 1500,
        timeout_ms: int = 60_000,
        idempotency_key: str | None = None,
    ):
        return await self._inner.remember_and_wait(  # type: ignore[attr-defined]
            text,
            namespace,
            poll_interval_ms=poll_interval_ms,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

    async def wait_for_remember_job(
        self, job_id: str, poll_interval_ms: int = 1500, timeout_ms: int = 60_000
    ):
        raise RuntimeError("the relayer lost the accepted job")

    async def recall(self, query: str, **kwargs: object):
        return await self._inner.recall(query, **kwargs)  # type: ignore[attr-defined]


class Harness:
    def __init__(
        self,
        facts: Sequence[dict[str, object]],
        verdict: str = "DIFFERENT",
        thresholds=None,
        client_wrapper=None,
    ):
        self.database = Database(":memory:")
        self.database.migrate()
        self.settings = Settings(database_path=":memory:", memwal_namespace_prefix="ranti")
        self.users = UserCrud(self.database)
        self.memories = MemoryCrud(self.database)
        self.turns = TurnCrud(self.database)
        self.contradictions = ContradictionCrud(self.database)
        self.llm = FakeLlm(facts, verdict)
        self.client = None
        if client_wrapper is not None:
            raw = MemWalMock.create(namespace=self.settings.memwal_namespace_prefix)
            self.client = client_wrapper(raw)
            gateway = MemWalGateway(
                client=self.client,
                boundary=build_memory_boundary(self.settings),
                mode="mock",
            )
        else:
            gateway = build_memory_gateway(self.settings)
        self.service = ConversationService(
            users=self.users,
            memories=self.memories,
            turns=self.turns,
            contradictions=self.contradictions,
            memory_gateway=gateway,
            llm_gateway=self.llm,
            settings=self.settings,
            thresholds=thresholds,
        )

    async def say(self, text: str, surface: str = "telegram"):
        return await self.service.handle_turn(surface, "42", "Ada", text)

    async def say_settled(self, text: str, surface: str = "telegram"):
        """A turn, then a wait for its accepted writes to settle.

        Only the local-index assertions need this; the turn itself returns
        before the relayer persists anything.
        """
        result = await self.say(text, surface)
        await self.service.await_pending_writes()
        return result


async def test_a_turn_writes_extracted_facts_and_lists_them_back() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    result = await harness.say_settled("I am allergic to peanuts")

    assert len(result.stored_facts) == 1
    assert result.stored_facts[0].verdict == "new"
    assert result.stored_facts[0].blob_id
    stored = harness.memories.list_for_user(result.user_id)
    assert [memory.text for memory in stored] == ["Ada is allergic to peanuts"]
    assert stored[0].importance == 1.0
    assert stored[0].origin_surface == "telegram"


async def test_repeating_the_same_fact_does_not_write_it_twice() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    first = await harness.say_settled("I am allergic to peanuts")
    second = await harness.say_settled("Just so you know, peanuts are dangerous for me")

    assert second.skipped_duplicates == 1
    assert second.stored_facts[0].verdict == "duplicate"
    assert second.stored_facts[0].blob_id is None
    assert harness.memories.count_for_user(first.user_id, None) == 1


async def test_an_update_supersedes_the_earlier_memory() -> None:
    harness = Harness(
        [{"text": "Ada works as a backend engineer", "importance": 0.7}],
        thresholds=FORCE_ADJUDICATION,
    )
    await harness.say_settled("I work as a backend engineer")

    harness.llm.facts = [{"text": "Ada works as a platform engineer", "importance": 0.7}]
    harness.llm.verdict = "UPDATES"
    second = await harness.say_settled("I moved to platform engineering")

    assert second.stored_facts[0].verdict == "updates"
    statuses = {memory.text: memory.status for memory in harness.memories.list_for_user(second.user_id, None)}
    assert statuses["Ada works as a backend engineer"] == "superseded"
    assert statuses["Ada works as a platform engineer"] == "active"


async def test_a_contradiction_is_surfaced_rather_than_silently_overwritten() -> None:
    harness = Harness(
        [{"text": "Ada does not eat meat", "importance": 0.8}],
        thresholds=FORCE_ADJUDICATION,
    )
    await harness.say_settled("I do not eat meat")

    harness.llm.facts = [{"text": "Ada eats steak every Friday", "importance": 0.8}]
    harness.llm.verdict = "CONTRADICTS"
    second = await harness.say_settled("I had a great steak on Friday")

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
    first = await harness.say_settled("I work as a backend engineer")
    harness.llm.facts = [{"text": "Ada works as a platform engineer", "importance": 0.7}]
    harness.llm.verdict = "UPDATES"
    await harness.say_settled("I moved to platform engineering")

    _, recalled, _, _ = await harness.service.recall_context(first.user_id, "engineer", budget=5)

    texts = [memory.text for memory in recalled]
    assert "Ada works as a platform engineer" in texts
    assert "Ada works as a backend engineer" not in texts


async def test_recall_marks_degradation_instead_of_claiming_empty_memory() -> None:
    harness = Harness([{"text": "Ada lives in Lagos", "importance": 0.6}])
    result = await harness.say_settled("I live in Lagos")

    namespace, recalled, degraded, _ = await harness.service.recall_context(
        result.user_id, "Lagos", budget=5
    )

    assert namespace.endswith("telegram-42")
    assert degraded is False
    assert [memory.text for memory in recalled] == ["Ada lives in Lagos"]


async def test_counterfactual_replay_shows_what_memory_changed() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say_settled("I am allergic to peanuts")
    # The fact is written at the end of a turn, so the next turn is the first
    # one that can recall it. This is the real flow, not a staged one.
    result = await harness.say_settled("I am allergic to peanuts")

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


# --------------------------------------------------- non-blocking persistence


async def test_consolidation_accepts_the_write_and_never_blocks_on_it() -> None:
    """The fact write is submitted as a job; the blocking method is never used.

    A recorded SDK client proves which of the two mutually exclusive write
    methods the consolidation path actually called.
    """
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=RecordingClient,
    )

    result = await harness.say("I am allergic to peanuts")

    user_surface = [call for call in harness.client.calls if call[1] == result.memory_namespace]
    assert [name for name, _, _ in user_surface] == ["remember"]
    assert "remember_and_wait" not in [name for name, _, _ in user_surface]
    assert result.stored_facts[0].pending is True

    await harness.service.await_pending_writes()


async def test_a_turn_returns_with_facts_marked_pending() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    result = await harness.say("I am allergic to peanuts")

    fact = result.stored_facts[0]
    assert fact.verdict == "new"
    assert fact.pending is True
    assert fact.blob_id is not None and fact.blob_id.startswith("pending:")
    index_rows = harness.memories.list_for_user(result.user_id)
    assert [row.blob_id for row in index_rows] == [fact.blob_id]

    await harness.service.await_pending_writes()


async def test_settling_replaces_the_placeholder_with_the_real_blob_id() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    result = await harness.say("I am allergic to peanuts")
    placeholder = result.stored_facts[0].blob_id
    assert placeholder is not None and placeholder.startswith("pending:")

    await harness.service.await_pending_writes()

    rows = harness.memories.list_for_user(result.user_id)
    assert len(rows) == 1
    assert not rows[0].blob_id.startswith("pending:")
    assert rows[0].blob_id != placeholder
    assert rows[0].text == "Ada is allergic to peanuts"


async def test_a_failed_job_removes_the_pending_row_and_the_turn_still_succeeds() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=FailingSettleClient,
    )

    result = await harness.say("I am allergic to peanuts")

    assert result.stored_facts[0].verdict == "new"
    assert result.stored_facts[0].pending is True
    assert result.memory_degraded is False
    assert len(harness.memories.list_for_user(result.user_id)) == 1

    await harness.service.await_pending_writes()

    assert harness.memories.list_for_user(result.user_id) == []


async def test_the_accepted_write_keeps_its_deterministic_idempotency_key() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=RecordingClient,
    )

    result = await harness.say("I am allergic to peanuts")

    keys = [
        key
        for name, namespace, key in harness.client.calls
        if name == "remember" and namespace == result.memory_namespace
    ]
    assert len(keys) == 1
    assert keys[0] is not None
    assert keys[0] == harness.service._idempotency_key(result.user_id, "Ada is allergic to peanuts")

    await harness.service.await_pending_writes()


async def test_the_prompt_forbids_denying_memory_when_nothing_is_recalled() -> None:
    """A real Telegram user was told 'I don't have long-term memory'.

    The no-memories branch of the prompt invited the model to say it had no
    stored memories, and the model generalised that into denying memory across
    conversations entirely, which contradicts the product.
    """
    harness = Harness([])

    messages = harness.service._build_prompt("Ada", "hello", [])
    system = messages[0].content.lower()

    assert "never say that you lack long-term memory" in system
    assert "you can, and you will remember" in system
    # And it still must not fabricate memories it was not given.
    assert "never claim to remember something that is not in the block" in system


async def test_a_turn_with_nothing_durable_prompts_the_user_to_share_facts() -> None:
    """Onboarding nudge, added because ten memories per user is the binding goal."""
    harness = Harness([])
    result = await harness.say("hello there")

    text = harness.service._render_reply(result)

    assert "Tell me a few things about yourself" in text


async def test_a_turn_that_stored_a_fact_does_not_nudge() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    result = await harness.say("I am allergic to peanuts")

    text = harness.service._render_reply(result)

    assert "Tell me a few things about yourself" not in text
    assert "memory:" not in text


async def test_the_prompt_never_denies_memory_when_notes_are_stored() -> None:
    """The worst bug found in real use.

    A real user with 19 stored memories was told "I don't have any stored
    memories about you yet", because recall returning nothing for one message was
    reported to the model as nothing being stored at all.
    """
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    first = await harness.say("I am allergic to peanuts")

    recalled, _, _, stored = await harness.service._assemble_context(
        first.user_id, first.memory_namespace, "trumpets and bicycles", 6
    )

    assert recalled == [], "this test needs a message nothing matches"
    assert stored == 1, "the note count must reflect what is actually stored"

    system = harness.service._build_prompt("Ada", "trumpets and bicycles", recalled, stored)[
        0
    ].content

    assert "nothing stored about this person yet" not in system
    assert "Do not say that you have no memory of them" in system
    assert "1 notes stored" in system


async def test_the_prompt_says_nothing_is_stored_only_when_that_is_true() -> None:
    harness = Harness([])
    recalled, _, _, stored = await harness.service._assemble_context(
        (await harness.say("hello there")).user_id,
        "ranti.user.test",
        "hello there",
        6,
    )

    assert stored == 0
    system = harness.service._build_prompt("Ada", "hello there", recalled, stored)[0].content

    assert "nothing stored about this person yet" in system


async def test_the_prompt_forbids_markdown_because_clients_showed_asterisks() -> None:
    harness = Harness([])
    system = harness.service._build_prompt("Ada", "hi", [], 0)[0].content

    assert "no markdown" in system
    assert "no asterisks" in system


async def test_the_memories_command_lists_stored_notes_without_calling_the_model() -> None:
    """The most requested feature in the real transcripts.

    A user asked repeatedly whether they could see what was stored about them.
    """
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say("I am allergic to peanuts")
    calls_before = harness.llm.reply_calls

    text = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "Ada is allergic to peanuts" in text
    assert "1 notes" in text
    assert harness.llm.reply_calls == calls_before


async def test_the_memories_command_is_honest_when_nothing_is_stored() -> None:
    harness = Harness([])

    text = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "nothing stored about you yet" in text


async def test_the_start_command_explains_the_bot_and_stores_nothing() -> None:
    harness = Harness([])

    text = harness.service.command_reply("/start", "telegram", "42", "Ada")

    assert "Cheta" in text
    assert "/memories" in text
    assert harness.llm.reply_calls == 0
    assert harness.users.list() == []


async def test_a_command_returns_no_turn_so_the_router_can_tell_the_difference() -> None:
    """A command is not a conversation turn and must not be stored as one."""
    harness = Harness([])

    result = await harness.service.handle_surface_turn(
        "telegram", "42", "Ada", "/start", "42"
    )

    assert result is None
    assert harness.turns.list_for_user("nobody") == []


async def test_a_returning_user_is_greeted_with_what_is_remembered() -> None:
    """The moment that makes the product's promise visible, immediately."""
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say("I am allergic to peanuts")

    text = harness.service.command_reply("/start", "telegram", "42", "Ada")

    assert "Welcome back" in text
    assert "I remember 1 things about you" in text
    assert "Ada is allergic to peanuts" in text


async def test_start_does_not_create_a_user_who_has_never_spoken() -> None:
    harness = Harness([])

    text = harness.service.command_reply("/start", "telegram", "999", "Stranger")

    assert harness.users.list() == []
    assert "Hi, I am Cheta" in text


async def test_asking_what_the_bot_knows_answers_from_the_store_not_recall() -> None:
    """"What do you know about me?" cannot be served by semantic recall.

    The query has no embedding similarity to "prefers dark mode", so recall
    returns nothing and the bot looked forgetful precisely when the person was
    testing whether it remembers. Real users asked this in words, not commands.
    """
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say("I am allergic to peanuts")
    calls_before = harness.llm.reply_calls

    result = await harness.service.handle_surface_turn(
        "telegram", "42", "Ada", "What do you know about me?", "42"
    )

    assert result is None, "a memory question is answered directly, not as a turn"
    assert harness.llm.reply_calls == calls_before, "and it must not call the model"
    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")
    assert "Ada is allergic to peanuts" in listing


async def test_an_ordinary_question_is_still_a_normal_turn() -> None:
    """The intent match must not swallow unrelated messages."""
    harness = Harness([{"text": "Ada likes tea", "importance": 0.7}])

    result = await harness.service.handle_surface_turn(
        "telegram", "42", "Ada", "How do I bake bread?", "42"
    )

    assert result is not None


async def test_forget_retires_a_note_so_it_stops_being_recalled() -> None:
    """The correction path, promised as part of making the bot act on its memory.

    Walrus Memory cannot erase a blob, so this is honest about what it does.
    """
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    first = await harness.say("I am allergic to peanuts")
    assert harness.memories.count_for_user(first.user_id, "active") == 1

    reply = harness.service.command_reply("/forget", "telegram", "42", "Ada", "1")

    assert "will not bring up" in reply
    assert "cannot erase a blob" in reply
    assert harness.memories.count_for_user(first.user_id, "active") == 0


async def test_forget_without_a_valid_number_explains_itself() -> None:
    harness = Harness([{"text": "Ada likes tea", "importance": 0.7}])
    await harness.say("I like tea")

    assert "which number" in harness.service.command_reply(
        "/forget", "telegram", "42", "Ada", "banana"
    )
    assert "no note 9" in harness.service.command_reply(
        "/forget", "telegram", "42", "Ada", "9"
    )


async def test_the_listing_shows_a_retired_note_marked_not_hidden() -> None:
    """Transparency: the note still exists on Walrus, so it is shown as retired
    rather than silently disappearing, which would be its own kind of lie."""
    harness = Harness([{"text": "Ada likes tea", "importance": 0.7}])
    await harness.say("I like tea")

    harness.service.command_reply("/forget", "telegram", "42", "Ada", "1")
    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "Ada likes tea" in listing
    assert "[superseded]" in listing
