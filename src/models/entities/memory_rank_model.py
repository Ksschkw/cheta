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
    """Bands used to decide what a newly extracted fact is.

    The upper bound matches the published distance bands in the Walrus Memory
    docs, where 0.25 to 0.55 reads as "related". Above that, recall is weak or
    unrelated and adjudicating every hit would cost a model call for noise.
    """

    duplicate_distance: float = 0.14
    duplicate_similarity: float = 0.72
    related_distance: float = 0.55


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


NEGATION_MARKERS = (" not ", " no ", " never ", " don't ", " doesn't ", " do not ", " does not ")


def normalise_tokens(text: str) -> frozenset[str]:
    tokens = {
        "".join(character for character in token if character.isalnum()).lower()
        for token in text.split()
    }
    return frozenset(token for token in tokens if len(token) > 3 and token not in STOP_TOKENS)


def normalise_text(text: str) -> str:
    """Whitespace-collapsed, case-folded text for deterministic equality.

    Two records are the same fact when this matches exactly, whatever any
    distance or similarity score happens to be. That check is cheap and cannot
    be defeated by an embedder.
    """
    return " ".join(text.split()).strip().casefold()


def exact_fact_match(left: str, right: str) -> bool:
    """True when two records say the identical thing, ignoring case and spacing."""
    if not left.strip() or not right.strip():
        return False
    return normalise_text(left) == normalise_text(right)


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of normalised tokens. Zero when either side is empty."""
    left_tokens = normalise_tokens(left)
    right_tokens = normalise_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _stem(token: str) -> str:
    """Cheap plural strip, so "owns" and "own" are the same comparison token."""
    if len(token) >= 4 and token.endswith("s"):
        return token[:-1]
    return token


def _comparison_tokens(text: str) -> frozenset[str]:
    return frozenset(_stem(token) for token in normalise_tokens(text))


def _comparison_tokens_in_order(text: str) -> list[str]:
    ordered: list[str] = []
    for token in text.split():
        cleaned = "".join(character for character in token if character.isalnum()).lower()
        if len(cleaned) <= 3 or cleaned in STOP_TOKENS:
            continue
        stemmed = _stem(cleaned)
        if stemmed not in ordered:
            ordered.append(stemmed)
    return ordered


def _negated(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(marker in lowered for marker in NEGATION_MARKERS)


def _all_tokens(text: str) -> frozenset[str]:
    """Every alphanumeric token, including numbers and stopwords.

    Containment must keep numbers: "Fact number 1" and "Fact number 2" are
    different facts, and a tokenizer that dropped digits or short words would
    call them the same.
    """
    tokens = {
        "".join(character for character in token if character.isalnum()).lower()
        for token in text.split()
    }
    return frozenset(token for token in tokens if token)


def contains_fact(inner: str, outer: str) -> bool:
    """True when one fact is a restatement of the other with extra words.

    "The user is a software engineering student" is contained by the same
    sentence plus "at FUTO": every token of the shorter one appears in the
    longer one. A single shared token is not enough to call two statements the
    same, so at least two must be shared.
    """
    inner_tokens = _all_tokens(inner)
    outer_tokens = _all_tokens(outer)
    if not inner_tokens or not outer_tokens:
        return False
    shared = inner_tokens & outer_tokens
    if len(shared) < 2:
        return False
    return inner_tokens <= outer_tokens or outer_tokens <= inner_tokens


def contradicts(left: str, right: str) -> bool:
    """A deterministic contradiction signal that does not need the model.

    Two statements conflict when they share an attribute but disagree on
    negation ("owns an Itel Power Go phone" against "does not own a phone", or
    "portable power station, not a phone"). The model still adjudicates the
    harder cases; this only removes the ones that are plainly self-conflicting
    from the append-only store.
    """
    if _negated(left) == _negated(right):
        return False
    left_tokens = _comparison_tokens(left)
    right_tokens = _comparison_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = left_tokens & right_tokens
    if not shared:
        return False
    if len(shared) >= 2 and len(shared) / min(len(left_tokens), len(right_tokens)) >= 0.5:
        return True
    left_order = _comparison_tokens_in_order(left)
    right_order = _comparison_tokens_in_order(right)
    return bool(left_order and right_order and left_order[0] == right_order[0])


def classify_candidate(
    candidate_text: str,
    neighbours: Sequence[RankedMemory],
    thresholds: ConsolidationThresholds,
    similarity_fn: Callable[[str, str], float] = similarity,
) -> tuple[str, RankedMemory | None]:
    """Return (verdict, best_neighbour).

    ``duplicate`` means do not write it: the space already says this.
    ``contradicts`` means the older statement cannot both be true and must be
    retired. ``related`` means a model must adjudicate whether it updates or
    contradicts. ``new`` means write it.
    """
    active = [neighbour for neighbour in neighbours if neighbour.status == "active"]

    # An exact text match is a duplicate, full stop. This does not consult any
    # distance or similarity score, so an embedder that misses the pair cannot
    # let two identical strings become active.
    for neighbour in active:
        if exact_fact_match(candidate_text, neighbour.text):
            return "duplicate", neighbour

    best: RankedMemory | None = None
    best_key = -1.0

    for neighbour in active:
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
    # Contradiction is checked before containment: a restatement that adds a
    # negation is a conflict, not a longer version of the same fact.
    if contradicts(candidate_text, best.text):
        return "contradicts", best
    if contains_fact(candidate_text, best.text):
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
