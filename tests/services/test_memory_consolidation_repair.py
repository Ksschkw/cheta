"""Consolidation and repair on the exact records from the owner's real list.

These are the regressions the owner actually hit: a duplicate MacBook note, a
student note with two levels of detail, a phone that became a power station, and
facts about the assistant sitting in a person's memory list.
"""

from __future__ import annotations

from tests.services.test_conversation_service import Harness

STUDENT_SHORT = "The user is a software engineering student."
STUDENT_LONG = (
    "The user is a software engineering student at the Federal University of "
    "Technology Owerri (FUTO)."
)
MACBOOK = "The user is interested in purchasing a MacBook."
PHONE = "The user currently owns an Itel Power Go phone."
POWER_STATION = (
    "The user owns an Itel Power Go, which is a portable power station, not a phone."
)
ASSISTANT_FACT = (
    "The assistant's name is Cheta, though some people still call them Ranti."
)


def seed(
    harness: Harness,
    text: str,
    blob_id: str,
    occurred_at: str,
    importance: float = 0.7,
) -> str:
    user = harness.users.get_or_create("telegram", "42", "Ada")
    harness.memories.create(
        user_id=user.id,
        blob_id=blob_id,
        namespace=harness.settings.memory_namespace(user.memory_key),
        text=text,
        importance=importance,
        origin_surface="telegram",
        occurred_at=occurred_at,
    )
    return user.id


def statuses(harness: Harness, user_id: str) -> dict[str, str]:
    return {
        record.text: record.status
        for record in harness.memories.list_for_user(user_id, None, 100)
    }


async def test_an_exact_duplicate_cannot_both_be_active() -> None:
    harness = Harness([])
    user_id = seed(harness, MACBOOK, "blob-11", "2026-01-01T00:00:00+00:00")

    harness.llm.facts = [{"text": MACBOOK, "importance": 0.7}]
    result = await harness.say("I am thinking about a MacBook")

    assert result.stored_facts[0].verdict == "duplicate"
    assert result.stored_facts[0].reason is not None
    assert "exact duplicate" in result.stored_facts[0].reason
    assert harness.memories.count_for_user(user_id, "active") == 1


async def test_an_exact_duplicate_within_one_batch_is_written_once() -> None:
    """A record accepted earlier in the same turn is not recallable yet."""
    harness = Harness([])
    user = harness.users.get_or_create("telegram", "42", "Ada")

    harness.llm.facts = [
        {"text": MACBOOK, "importance": 0.7},
        {"text": MACBOOK, "importance": 0.7},
    ]
    result = await harness.say_settled("I am thinking about a MacBook")

    assert [fact.verdict for fact in result.stored_facts] == ["new", "duplicate"]
    assert harness.memories.count_for_user(user.id, "active") == 1


async def test_the_seeded_near_duplicate_pair_collapses_on_listing() -> None:
    harness = Harness([])
    user_id = seed(harness, STUDENT_SHORT, "blob-6", "2026-01-01T00:00:00+00:00")
    seed(harness, STUDENT_LONG, "blob-7", "2026-01-02T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "duplicate retired" in listing
    assert "cleaned up 1 problem record" in listing
    state = statuses(harness, user_id)
    assert state[STUDENT_SHORT] == "superseded"
    assert state[STUDENT_LONG] == "active"


async def test_the_seeded_contradiction_retires_the_older_one() -> None:
    harness = Harness([])
    user_id = seed(harness, PHONE, "blob-10", "2026-01-01T00:00:00+00:00")
    seed(harness, POWER_STATION, "blob-12", "2026-01-02T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "older conflicting note retired" in listing
    state = statuses(harness, user_id)
    assert state[PHONE] == "contradicted"
    assert state[POWER_STATION] == "active"


async def test_an_exact_duplicate_pair_collapses_on_listing() -> None:
    harness = Harness([])
    user_id = seed(harness, MACBOOK, "blob-11", "2026-01-01T00:00:00+00:00")
    seed(harness, MACBOOK, "blob-13", "2026-01-02T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "duplicate retired" in listing
    active = [
        record
        for record in harness.memories.list_for_user(user_id, "active", 100)
        if record.text == MACBOOK
    ]
    assert len(active) == 1


async def test_a_fact_about_the_assistant_is_not_stored() -> None:
    harness = Harness([])

    harness.llm.facts = [{"text": ASSISTANT_FACT, "importance": 0.7}]
    result = await harness.say("What is your name?")

    assert result.stored_facts[0].verdict == "rejected"
    assert "not a durable fact about the person" in (result.stored_facts[0].reason or "")
    assert harness.memories.count_for_user(result.user_id, "active") == 0


async def test_an_existing_fact_about_the_assistant_is_not_shown() -> None:
    harness = Harness([])
    user_id = seed(harness, ASSISTANT_FACT, "blob-a", "2026-01-01T00:00:00+00:00")
    seed(harness, MACBOOK, "blob-b", "2026-01-02T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")
    greeting = harness.service.command_reply("/start", "telegram", "42", "Ada")

    assert "assistant" not in listing.lower()
    assert "still call them Ranti" not in listing
    assert "You are interested in purchasing a MacBook." in listing
    assert "still call them Ranti" not in greeting
    # The record is retired locally so it stops being recalled as well.
    assert statuses(harness, user_id)[ASSISTANT_FACT] == "superseded"


async def test_the_listing_renders_stored_facts_in_the_second_person() -> None:
    harness = Harness([])
    seed(harness, STUDENT_SHORT, "blob-6", "2026-01-01T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "1. You are a software engineering student." in listing
    assert "The user is" not in listing


async def test_the_resume_line_names_memories_in_the_second_person() -> None:
    harness = Harness([])
    await harness.say_settled("hello")
    seed(harness, "The user prefers jollof rice and beans.", "blob-j", "2026-01-01T00:00:00+00:00")
    harness.age_all_turns(10.0)

    result = await harness.say("Hello again")

    assert result.resume_note is not None
    assert "you prefer jollof rice and beans" in result.reply
    assert "The user prefers" not in result.reply


async def test_repair_leaves_a_genuinely_different_pair_alone() -> None:
    harness = Harness([])
    user_id = seed(harness, MACBOOK, "blob-11", "2026-01-01T00:00:00+00:00")
    seed(harness, "Ada plays the trumpet.", "blob-t", "2026-01-02T00:00:00+00:00")

    listing = harness.service.command_reply("/memories", "telegram", "42", "Ada")

    assert "cleaned up" not in listing
    assert harness.memories.count_for_user(user_id, "active") == 2


async def test_a_contradiction_retires_a_record_whose_local_blob_is_still_pending() -> None:
    """A recall hit carries the real blob id while the index row is a placeholder.

    On the real relayer a write settles for tens of seconds. If the next fact
    arrives first, the retirement used to look the old record up by a blob id
    the local row did not have yet, silently left both active.
    """
    harness = Harness([])
    user = harness.users.get_or_create("telegram", "42", "Ada")
    namespace = harness.settings.memory_namespace(user.memory_key)
    # The relayer copy that recall will find.
    await harness.service._memory.remember(PHONE, namespace)
    # The local index row still carries the placeholder.
    harness.memories.create(
        user_id=user.id,
        blob_id="pending:old-job",
        namespace=namespace,
        text=PHONE,
        importance=0.7,
        origin_surface="telegram",
        occurred_at="2026-01-01T00:00:00+00:00",
    )

    harness.llm.facts = [{"text": POWER_STATION, "importance": 0.7}]
    result = await harness.say_settled("I am describing my power station")

    assert result.stored_facts[0].verdict == "contradicts"
    assert statuses(harness, user.id)[PHONE] == "contradicted"
