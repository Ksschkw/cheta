"""The consolidation and recall policy, tested directly.

This is the layer that decides what earns a place in the context window. It is
also the part of the system most likely to be wrong in a way that looks fine:
wrong recalls do not raise, they just quietly fill the prompt with the wrong
facts. So the policy is tested here on its own, with numbers chosen so that the
expected ordering is arithmetic rather than opinion.
"""

from __future__ import annotations

import pytest

from models.entities.memory_rank_model import (
    ConsolidationThresholds,
    RankedMemory,
    RankingWeights,
    classify_candidate,
    normalise_tokens,
    select_context,
    similarity,
)

WEIGHTS = RankingWeights(
    semantic=0.55,
    recency=0.25,
    importance=0.20,
    half_life_days=30.0,
    min_semantic=0.30,
    duplicate_similarity=0.72,
)


def memory(
    blob_id: str,
    text: str,
    distance: float,
    importance: float = 0.5,
    age_days: float = 0.0,
    status: str = "active",
    superseded_by: str | None = None,
) -> RankedMemory:
    return RankedMemory(
        blob_id=blob_id,
        text=text,
        distance=distance,
        importance=importance,
        age_days=age_days,
        status=status,
        superseded_by=superseded_by,
    )


# ------------------------------------------------------------------ scoring


def test_semantic_score_is_one_minus_distance_and_clamped() -> None:
    assert memory("a", "x", 0.0).semantic == 1.0
    assert memory("a", "x", 0.4).semantic == pytest.approx(0.6)
    assert memory("a", "x", 2.5).semantic == 0.0


def test_recency_halves_over_one_half_life() -> None:
    fresh = memory("a", "x", 0.5, age_days=0.0)
    half_life_old = memory("b", "x", 0.5, age_days=30.0)

    assert fresh.recency(30.0) == pytest.approx(1.0)
    assert half_life_old.recency(30.0) == pytest.approx(0.5)
    assert half_life_old.recency(0.0) == 1.0


def test_salience_combines_the_three_signals_with_the_configured_weights() -> None:
    candidate = memory("a", "x", 0.2, importance=0.9, age_days=0.0)

    # 0.55 * 0.8 + 0.25 * 1.0 + 0.20 * 0.9
    assert candidate.salience(WEIGHTS) == pytest.approx(0.44 + 0.25 + 0.18)


def test_importance_only_matters_when_it_is_weighted() -> None:
    candidate = memory("a", "x", 0.2, importance=1.0)
    no_importance = RankingWeights(semantic=0.75, recency=0.25, importance=0.0)
    with_importance = RankingWeights(semantic=0.5, recency=0.25, importance=0.25)

    assert candidate.salience(no_importance) == pytest.approx(0.75 * 0.8 + 0.25)
    assert candidate.salience(with_importance) == pytest.approx(0.5 * 0.8 + 0.25 + 0.25)


# --------------------------------------------------------------- tokenising


def test_similarity_ignores_stopwords_and_short_tokens() -> None:
    assert normalise_tokens("The user is a kitten") == frozenset({"kitten"})
    assert normalise_tokens("The user is a cat") == frozenset()
    assert similarity("Ada is allergic to peanuts", "Ada is allergic to peanuts") == 1.0
    assert similarity("Lagos", "Lagos") == 1.0
    assert similarity("Lagos", "trumpet") == 0.0
    assert similarity("", "anything") == 0.0


# ------------------------------------------------------------- select_context


def test_salience_ordering_is_arithmetic_not_incidental() -> None:
    """B outranks A on importance, A outranks C on recency."""
    candidates = [
        memory("A", "Ada likes jollof rice", 0.10, importance=0.5, age_days=0.0),
        memory("B", "Ada speaks fluent Yoruba", 0.20, importance=0.9, age_days=0.0),
        memory("C", "Ada once visited Accra", 0.15, importance=0.5, age_days=60.0),
    ]

    selected = select_context(candidates, WEIGHTS, budget=3)

    assert [item.blob_id for item in selected] == ["B", "A", "C"]


def test_a_superseded_memory_is_dropped_even_when_it_is_the_closest_match() -> None:
    candidates = [
        memory("OLD", "Ada works as a backend engineer", 0.01, status="superseded", superseded_by="NEW"),
        memory("NEW", "Ada works as a platform engineer", 0.40),
    ]

    selected = select_context(candidates, WEIGHTS, budget=5)

    assert [item.blob_id for item in selected] == ["NEW"]


def test_weak_matches_fall_below_the_relevance_floor() -> None:
    candidates = [
        memory("GOOD", "Ada is allergic to peanuts", 0.20),
        memory("FILLER", "Ada owns a blue bicycle", 0.75),
    ]

    selected = select_context(candidates, WEIGHTS, budget=5)

    assert [item.blob_id for item in selected] == ["GOOD"]


def test_near_duplicate_restatements_collapse_to_the_more_salient_one() -> None:
    candidates = [
        memory("LOW", "Ada is allergic to peanuts badly", 0.30, importance=0.2),
        memory("HIGH", "Ada is allergic to peanuts badly indeed", 0.10, importance=1.0),
    ]
    assert similarity(candidates[0].text, candidates[1].text) >= WEIGHTS.duplicate_similarity

    selected = select_context(candidates, WEIGHTS, budget=5)

    assert [item.blob_id for item in selected] == ["HIGH"]


def test_genuinely_different_facts_are_not_collapsed() -> None:
    candidates = [
        memory("A", "Ada is allergic to peanuts", 0.10),
        memory("B", "Ada plays the trumpet", 0.20),
    ]

    selected = select_context(candidates, WEIGHTS, budget=5)

    assert {item.blob_id for item in selected} == {"A", "B"}


def test_the_budget_is_respected_exactly_and_zero_yields_nothing() -> None:
    candidates = [
        memory("A", "Ada likes jollof rice", 0.10),
        memory("B", "Ada speaks fluent Yoruba", 0.20),
        memory("C", "Ada once visited Accra", 0.30),
    ]

    assert len(select_context(candidates, WEIGHTS, budget=2)) == 2
    assert len(select_context(candidates, WEIGHTS, budget=1)) == 1
    assert select_context(candidates, WEIGHTS, budget=0) == []


def test_recency_breaks_a_tie_between_otherwise_identical_memories() -> None:
    older = memory("OLD", "Ada lives in Lagos", 0.20, importance=0.5, age_days=120.0)
    newer = memory("NEW", "Ada works in Lagos", 0.20, importance=0.5, age_days=1.0)
    weights = RankingWeights(semantic=0.10, recency=0.90, importance=0.0, min_semantic=0.0)

    selected = select_context([older, newer], weights, budget=1)

    assert [item.blob_id for item in selected] == ["NEW"]


# ---------------------------------------------------------- classify_candidate


def test_a_near_identical_fact_is_a_duplicate_by_distance() -> None:
    thresholds = ConsolidationThresholds()
    neighbour = memory("N1", "Ada is allergic to peanuts", 0.05)

    verdict, best = classify_candidate("Ada is allergic to peanuts", [neighbour], thresholds)

    assert verdict == "duplicate"
    assert best is not None and best.blob_id == "N1"


def test_a_restatement_with_extra_words_is_a_duplicate_by_similarity() -> None:
    thresholds = ConsolidationThresholds()
    neighbour = memory("N1", "Ada is allergic to peanuts badly", 0.40)

    verdict, _ = classify_candidate("Ada is allergic to peanuts badly indeed", [neighbour], thresholds)

    assert verdict == "duplicate"


def test_a_close_but_different_fact_needs_adjudication() -> None:
    thresholds = ConsolidationThresholds()
    neighbour = memory("N1", "Ada works as a backend engineer", 0.30)

    verdict, best = classify_candidate("Ada owns a backend bicycle", [neighbour], thresholds)

    assert verdict == "related"
    assert best is not None and best.blob_id == "N1"


def test_an_unrelated_fact_is_new_and_carries_no_neighbour() -> None:
    thresholds = ConsolidationThresholds()
    neighbour = memory("N1", "Ada works as a backend engineer", 0.90)

    verdict, best = classify_candidate("Ada plays the trumpet", [neighbour], thresholds)

    assert verdict == "new"
    assert best is None


def test_adjudication_ignores_memories_that_are_already_superseded() -> None:
    thresholds = ConsolidationThresholds()
    stale = memory("STALE", "Ada works as a backend engineer", 0.02, status="superseded", superseded_by="X")

    verdict, best = classify_candidate("Ada works as a backend engineer", [stale], thresholds)

    assert verdict == "new"
    assert best is None


def test_an_exact_text_match_is_a_duplicate_whatever_the_distance_says() -> None:
    """The guard must not depend on the embedder or a similarity score."""
    thresholds = ConsolidationThresholds()
    neighbour = memory("N1", "The user is interested in purchasing a MacBook.", 0.90)

    verdict, best = classify_candidate(
        "The user is interested in purchasing a MacBook.", [neighbour], thresholds
    )

    assert verdict == "duplicate"
    assert best is not None and best.blob_id == "N1"


def test_a_contained_restatement_is_a_duplicate_not_just_related() -> None:
    """The measured #6/#7 pair: Jaccard 0.375, mock distance 0.50."""
    thresholds = ConsolidationThresholds()
    shorter = memory("N1", "The user is a software engineering student.", 0.50)

    verdict, _ = classify_candidate(
        "The user is a software engineering student at the Federal University of "
        "Technology Owerri (FUTO).",
        [shorter],
        thresholds,
    )

    assert verdict == "duplicate"


def test_a_shared_attribute_with_opposite_polarity_contradicts_without_a_model() -> None:
    """The measured #10/#12 pair: distance 0.4286 and a negated restatement."""
    thresholds = ConsolidationThresholds()
    phone = memory("N1", "The user currently owns an Itel Power Go phone.", 0.4286)

    verdict, best = classify_candidate(
        "The user owns an Itel Power Go, which is a portable power station, not a phone.",
        [phone],
        thresholds,
    )

    assert verdict == "contradicts"
    assert best is not None and best.blob_id == "N1"
