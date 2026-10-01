"""Second-person rendering and the local repair plan, tested as pure policy."""

from __future__ import annotations

from models.entities.memory_model import MemoryModel
from models.entities.memory_phrasing_model import (
    is_assistant_fact,
    is_person_fact,
    person_facing,
    subject_names,
    to_second_person,
)
from models.entities.memory_rank_model import contains_fact, exact_fact_match
from models.entities.memory_repair_model import (
    KIND_CONTRADICTION,
    KIND_DUPLICATE,
    KIND_NOT_ABOUT_PERSON,
    plan_repairs,
)

KOSI = "Kosisochukwu"
KOSI_NAMES = subject_names(KOSI)


def record(
    text: str,
    memory_id: str,
    blob_id: str,
    importance: float = 0.7,
    occurred_at: str = "2026-01-01T00:00:00+00:00",
    status: str = "active",
    superseded_by: str | None = None,
) -> MemoryModel:
    return MemoryModel(
        id=memory_id,
        user_id="user-1",
        blob_id=blob_id,
        namespace="ranti.user.test",
        text=text,
        importance=importance,
        origin_surface="telegram",
        status=status,
        superseded_by=superseded_by,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


# --------------------------------------------------------------- rendering


def test_rendering_turns_the_user_is_into_you_are_and_leaves_the_rest_alone() -> None:
    assert (
        to_second_person("The user is a software engineering student at FUTO.")
        == "You are a software engineering student at FUTO."
    )
    assert (
        to_second_person("The user's name is Kosisochukwu.")
        == "Your name is Kosisochukwu."
    )
    assert (
        to_second_person("The user holds a negative opinion of Lionel Messi.")
        == "You hold a negative opinion of Lionel Messi."
    )
    assert (
        to_second_person("The user currently owns an Itel Power Go phone.")
        == "You currently own an Itel Power Go phone."
    )
    # A record that does not match a known pattern is left exactly as it was.
    assert to_second_person("Ada is allergic to peanuts.") == "Ada is allergic to peanuts."
    assert to_second_person("Loves jazz.") == "Loves jazz."


def test_a_fact_about_the_assistant_is_never_rendered_as_a_fact_about_you() -> None:
    assistant = "The assistant's name is Cheta, though some people still call them Ranti."

    assert is_assistant_fact(assistant) is True
    assert is_person_fact(assistant) is False
    assert person_facing(assistant) is None
    assert person_facing("The user prefers jollof rice.") == "You prefer jollof rice."


def test_a_fact_about_the_conversation_is_not_a_fact_about_the_person() -> None:
    assert person_facing("The user said they were tired in this conversation.") is None
    assert person_facing("The user is allergic to peanuts.") is not None


# ------------------------------------------------------------- repair plan


def test_the_seeded_near_duplicate_pair_collapses_to_the_more_specific_one() -> None:
    actions = plan_repairs(
        [
            record("The user is a software engineering student.", "m6", "blob-6"),
            record(
                "The user is a software engineering student at the Federal University "
                "of Technology Owerri (FUTO).",
                "m7",
                "blob-7",
                occurred_at="2026-01-02T00:00:00+00:00",
            ),
        ]
    )

    duplicates = [action for action in actions if action.kind == KIND_DUPLICATE]
    assert [action.retired_id for action in duplicates] == ["m6"]
    assert duplicates[0].kept_id == "m7"
    assert duplicates[0].kept_blob_id == "blob-7"


def test_the_seeded_contradiction_retires_the_older_record() -> None:
    actions = plan_repairs(
        [
            record(
                "The user currently owns an Itel Power Go phone.",
                "m10",
                "blob-10",
                occurred_at="2026-01-01T00:00:00+00:00",
            ),
            record(
                "The user owns an Itel Power Go, which is a portable power station, "
                "not a phone.",
                "m12",
                "blob-12",
                occurred_at="2026-01-02T00:00:00+00:00",
            ),
        ]
    )

    contradictions = [action for action in actions if action.kind == KIND_CONTRADICTION]
    assert [action.retired_id for action in contradictions] == ["m10"]
    assert contradictions[0].kept_id == "m12"


def test_exact_duplicates_collapse_to_one_active_record() -> None:
    actions = plan_repairs(
        [
            record("The user is interested in purchasing a MacBook.", "m11", "blob-11"),
            record("The user is interested in purchasing a MacBook.", "m13", "blob-13"),
        ]
    )

    assert [action.kind for action in actions] == [KIND_DUPLICATE]
    assert actions[0].retired_id in {"m11", "m13"}


def test_a_record_about_the_assistant_is_retired_and_an_unmatched_one_is_not() -> None:
    actions = plan_repairs(
        [
            record(
                "The assistant's name is Cheta, though some people still call them Ranti.",
                "m-a",
                "blob-a",
            ),
            record("Ada is allergic to peanuts.", "m-b", "blob-b"),
        ]
    )

    assert [action.kind for action in actions] == [KIND_NOT_ABOUT_PERSON]
    assert actions[0].retired_id == "m-a"


# --------------------------------------------- rendering the person's own name


def test_rendering_turns_the_persons_own_display_name_into_you() -> None:
    assert (
        to_second_person("Kosisochukwu is a software engineering student.", KOSI_NAMES)
        == "You are a software engineering student."
    )
    assert (
        to_second_person(
            "Kosisochukwu's final year project is titled 'Multi-Agent Reinforcement "
            "Learning for Adaptive TCP Congestion Control'.",
            KOSI_NAMES,
        )
        == "Your final year project is titled 'Multi-Agent Reinforcement Learning "
        "for Adaptive TCP Congestion Control'."
    )
    assert (
        to_second_person("Kosisochukwu studies software engineering.", KOSI_NAMES)
        == "You study software engineering."
    )
    assert (
        to_second_person("Kosisochukwu has a car.", KOSI_NAMES) == "You have a car."
    )


def test_a_sentence_that_merely_mentions_the_name_is_left_alone() -> None:
    text = "You told Kosisochukwu about it"

    assert to_second_person(text, KOSI_NAMES) == text
    assert person_facing(text, KOSI_NAMES) == text


def test_a_plainly_different_pair_is_still_left_alone_after_normalisation() -> None:
    actions = plan_repairs(
        [
            record("Kosisochukwu is allergic to peanuts.", "m-a", "blob-a"),
            record("Kosisochukwu plays the trumpet.", "m-b", "blob-b"),
        ],
        KOSI_NAMES,
    )

    assert actions == []


# ------------------------------------- subject-resolved duplicate comparison


def test_a_named_subject_and_the_user_subject_are_one_claim_with_extra_detail() -> None:
    named = "Kosisochukwu is a software engineering student."
    detailed = (
        "The user is a software engineering student at the Federal University of "
        "Technology Owerri (FUTO)."
    )

    assert contains_fact(named, detailed, KOSI_NAMES) is True

    actions = plan_repairs(
        [
            record(named, "m6", "blob-6"),
            record(
                detailed,
                "m7",
                "blob-7",
                occurred_at="2026-01-02T00:00:00+00:00",
            ),
        ],
        KOSI_NAMES,
    )

    duplicates = [action for action in actions if action.kind == KIND_DUPLICATE]
    assert [action.retired_id for action in duplicates] == ["m6"]
    assert duplicates[0].kept_id == "m7"
    assert duplicates[0].kept_blob_id == "blob-7"


def test_an_exact_duplicate_is_still_collapsed_across_subjects() -> None:
    assert (
        exact_fact_match(
            "The user is interested in purchasing a MacBook.",
            "Kosisochukwu is interested in purchasing a MacBook.",
            KOSI_NAMES,
        )
        is True
    )
    actions = plan_repairs(
        [
            record("The user is interested in purchasing a MacBook.", "m11", "blob-11"),
            record(
                "Kosisochukwu is interested in purchasing a MacBook.",
                "m13",
                "blob-13",
                occurred_at="2026-01-02T00:00:00+00:00",
            ),
        ],
        KOSI_NAMES,
    )

    assert [action.kind for action in actions] == [KIND_DUPLICATE]
    # The deterministic exact-match guard, not the containment fallback, is what
    # caught this pair.
    assert actions[0].reason == "exact duplicate of an active record"
    assert actions[0].retired_id in {"m11", "m13"}


def test_a_contradiction_still_retires_the_older_record_after_normalisation() -> None:
    older = "Kosisochukwu currently owns an Itel Power Go phone."
    newer = (
        "The user owns an Itel Power Go, which is a portable power station, not a phone."
    )

    actions = plan_repairs(
        [
            record(older, "m10", "blob-10", occurred_at="2026-01-01T00:00:00+00:00"),
            record(newer, "m12", "blob-12", occurred_at="2026-01-02T00:00:00+00:00"),
        ],
        KOSI_NAMES,
    )

    contradictions = [action for action in actions if action.kind == KIND_CONTRADICTION]
    assert [action.retired_id for action in contradictions] == ["m10"]
    assert contradictions[0].kept_id == "m12"
