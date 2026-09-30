"""Consolidation and recall policy: the hippocampus.

Semantic recall alone answers "what is closest to this query". That is not the
same question as "what deserves the context window". Append-only storage plus
eager fact extraction means the closest results drift towards near-duplicates,
stale values and unresolved contradictions. This module is the pure policy that
decides what survives, what is redundant, and what is missing.

It is deliberately free of imports from this project so it can be reasoned about
and tested on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

STOP_TOKENS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "have",
        "has",
        "was",
        "were",
        "are",
        "but",
        "not",
        "you",
        "your",
        "his",
        "her",
        "its",
        "they",
        "them",
        "then",
        "than",
        "into",
        "about",
        "over",
        "under",
        "user",
        "users",
    }
)


@dataclass(frozen=True)
class RankingWeights:
    """Transparent, tunable and explainable. Never a learned black box."""

    semantic: float = 0.55
    recency: float = 0.25
    importance: float = 0.20
    half_life_days: float = 30.0
    min_semantic: float = 0.30
    duplicate_similarity: float = 0.72


@dataclass(frozen=True)
class ConsolidationThresholds:
    """Bands used to decide what a newly extracted fact is."""

    duplicate_distance: float = 0.14
    duplicate_similarity: float = 0.72
    related_distance: float = 0.45


@dataclass(frozen=True)
class RankedMemory:
    """A recall hit enriched with the metadata Walrus Memory does not return."""

    blob_id: str
    text: str
    distance: float
    importance: float
    age_days: float
    status: str = "active"
    superseded_by: str | None = None
    origin_surface: str = "unknown"

    @property
    def semantic(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.distance))

    def recency(self, half_life_days: float) -> float:
        if half_life_days <= 0:
            return 1.0
        return 0.5 ** (max(0.0, self.age_days) / half_life_days)

    def salience(self, weights: RankingWeights) -> float:
        return (
            weights.semantic * self.semantic
            + weights.recency * self.recency(weights.half_life_days)
            + weights.importance * self.importance
        )


def normalise_tokens(text: str) -> frozenset[str]:
    tokens = {
        "".join(character for character in token if character.isalnum()).lower()
        for token in text.split()
    }
    return frozenset(token for token in tokens if len(token) > 3 and token not in STOP_TOKENS)


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of normalised tokens. Zero when either side is empty."""
    left_tokens = normalise_tokens(left)
    right_tokens = normalise_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def classify_candidate(
    candidate_text: str,
    neighbours: Sequence[RankedMemory],
    thresholds: ConsolidationThresholds,
    similarity_fn: Callable[[str, str], float] = similarity,
) -> tuple[str, RankedMemory | None]:
    """Return (verdict, best_neighbour).

    ``duplicate`` means do not write it: the space already says this.
    ``related`` means a model must adjudicate whether it updates or contradicts.
    ``new`` means write it.
    """
    best: RankedMemory | None = None
    best_key = -1.0

    for neighbour in neighbours:
        if neighbour.status != "active":
            continue
        key = max(neighbour.semantic, similarity_fn(candidate_text, neighbour.text))
        if key > best_key:
            best_key = key
            best = neighbour

    if best is None:
        return "new", None

    if best.distance <= thresholds.duplicate_distance:
        return "duplicate", best
    if similarity_fn(candidate_text, best.text) >= thresholds.duplicate_similarity:
        return "duplicate", best
    if best.distance <= thresholds.related_distance:
        return "related", best
    return "new", None


def select_context(
    candidates: Sequence[RankedMemory],
    weights: RankingWeights,
    budget: int,
    similarity_fn: Callable[[str, str], float] = similarity,
) -> list[RankedMemory]:
    """Consolidate a wide candidate set down to what the context window gets.

    Superseded memories are dropped, weak matches are dropped, near-duplicates
    collapse to the highest-salience restatement, and the result is capped.
    """
    if budget <= 0:
        return []

    live = [candidate for candidate in candidates if candidate.status == "active"]
    relevant = [candidate for candidate in live if candidate.semantic >= weights.min_semantic]
    ordered = sorted(relevant, key=lambda candidate: candidate.salience(weights), reverse=True)

    selected: list[RankedMemory] = []
    for candidate in ordered:
        if len(selected) >= budget:
            break
        if any(
            similarity_fn(candidate.text, chosen.text) >= weights.duplicate_similarity
            for chosen in selected
        ):
            continue
        selected.append(candidate)
    return selected
