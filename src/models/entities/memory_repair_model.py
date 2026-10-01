"""Deciding which active records cannot both stay active.

The local index already holds data written before the write path learned to
reject exact duplicates, restatements, contradictions and facts about the
assistant. Append-only storage cannot rewrite those blobs, but the index that
drives recall and the listing can retire the redundant copy. This module is the
pure policy that decides what to retire; the services apply the decision through
the crud layer.

It is deliberately free of imports from outside this layer so the rules can be
tested on their own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from models.entities.memory_model import STATUS_ACTIVE
from models.entities.memory_phrasing_model import is_person_fact
from models.entities.memory_rank_model import (
    contains_fact,
    contradicts,
    normalise_text,
)

KIND_DUPLICATE = "duplicate"
KIND_CONTRADICTION = "contradiction"
KIND_NOT_ABOUT_PERSON = "not_about_person"


@dataclass(frozen=True)
class RepairAction:
    """One retirement the listing should perform and report."""

    kind: str
    retired_id: str
    retired_blob_id: str
    retired_text: str
    kept_id: str | None
    kept_blob_id: str | None
    kept_text: str | None
    reason: str


def _order_key(record) -> tuple[str, str]:
    return (record.occurred_at or record.created_at, record.id)


def _keeper_key(record) -> tuple[float, int, str]:
    # Most important first, then the more specific wording, then the newer one.
    return (
        -float(record.importance),
        -len(record.text),
        record.occurred_at or record.created_at,
    )


def plan_repairs(records: Sequence) -> list[RepairAction]:
    """Return the retirements that collapse obvious bad state.

    Exact duplicates keep one record and retire the rest. A contradiction
    retires the older statement. A record that is about the assistant or the
    conversation rather than the person is retired outright. Anything that does
    not match a known pattern is left alone.
    """
    active = [record for record in records if record.status == STATUS_ACTIVE]
    active.sort(key=_order_key)

    actions: list[RepairAction] = []
    remaining = []

    for record in active:
        if not is_person_fact(record.text):
            actions.append(
                RepairAction(
                    kind=KIND_NOT_ABOUT_PERSON,
                    retired_id=record.id,
                    retired_blob_id=record.blob_id,
                    retired_text=record.text,
                    kept_id=None,
                    kept_blob_id=None,
                    kept_text=None,
                    reason="the record is about the assistant or the conversation, not the person",
                )
            )
            continue
        remaining.append(record)

    grouped: dict[str, list] = {}
    for record in remaining:
        grouped.setdefault(normalise_text(record.text), []).append(record)

    deduplicated = []
    for group in grouped.values():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue
        ordered = sorted(group, key=_keeper_key)
        keeper = ordered[0]
        deduplicated.append(keeper)
        for duplicate in ordered[1:]:
            actions.append(
                RepairAction(
                    kind=KIND_DUPLICATE,
                    retired_id=duplicate.id,
                    retired_blob_id=duplicate.blob_id,
                    retired_text=duplicate.text,
                    kept_id=keeper.id,
                    kept_blob_id=keeper.blob_id,
                    kept_text=keeper.text,
                    reason="exact duplicate of an active record",
                )
            )
    deduplicated.sort(key=_order_key)

    retired: set[str] = set()
    for index, record in enumerate(deduplicated):
        if record.id in retired:
            continue
        for later in deduplicated[index + 1 :]:
            if later.id in retired:
                continue
            if contradicts(record.text, later.text):
                # The older statement is the one retired; the newer, later
                # statement is the one that stands. A conflict is checked before
                # containment so a negated restatement is not called a duplicate.
                actions.append(
                    RepairAction(
                        kind=KIND_CONTRADICTION,
                        retired_id=record.id,
                        retired_blob_id=record.blob_id,
                        retired_text=record.text,
                        kept_id=later.id,
                        kept_blob_id=later.blob_id,
                        kept_text=later.text,
                        reason=f"contradicts a later record: {later.text}",
                    )
                )
                retired.add(record.id)
                break
            if contains_fact(record.text, later.text):
                # A restatement with extra words. Keep the more specific (or
                # more important) wording and retire the other.
                keeper, loser = (
                    (record, later)
                    if _keeper_key(record) <= _keeper_key(later)
                    else (later, record)
                )
                actions.append(
                    RepairAction(
                        kind=KIND_DUPLICATE,
                        retired_id=loser.id,
                        retired_blob_id=loser.blob_id,
                        retired_text=loser.text,
                        kept_id=keeper.id,
                        kept_blob_id=keeper.blob_id,
                        kept_text=keeper.text,
                        reason="restatement of an active record",
                    )
                )
                retired.add(loser.id)
                if loser.id == record.id:
                    break
                continue

    return actions
