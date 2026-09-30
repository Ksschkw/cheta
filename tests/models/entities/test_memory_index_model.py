"""Wire-format tests for the portable index snapshot. Pure, no project wiring."""

from __future__ import annotations

import pytest

from models.entities.memory_index_model import (
    INDEX_MARKER,
    IndexedMemoryRecord,
    decode_snapshot,
    encode_snapshot,
    is_snapshot,
    select_snapshot_records,
)


def record(
    blob_id: str,
    text: str,
    status: str = "active",
    importance: float = 0.5,
    **overrides: object,
) -> IndexedMemoryRecord:
    fields: dict[str, object] = {
        "blob_id": blob_id,
        "text": text,
        "status": status,
        "importance": importance,
        "origin_surface": "telegram",
        "superseded_by": None,
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return IndexedMemoryRecord(**fields)  # type: ignore[arg-type]


def test_a_snapshot_round_trips_exactly() -> None:
    records = [
        record("blob-1", "Ada is allergic to peanuts", importance=1.0),
        record(
            "blob-2",
            "Ada works as a backend engineer",
            status="superseded",
            importance=0.7,
            superseded_by="blob-3",
        ),
        record(
            "blob-3",
            "Ada lives in Lagos",
            status="contradicted",
            importance=0.6,
        ),
    ]

    text = encode_snapshot(records, 7, "2026-09-30T21:54:00+00:00")

    sequence, decoded = decode_snapshot(text)

    assert sequence == 7
    assert decoded == records
    assert is_snapshot(text) is True
    assert text.startswith(INDEX_MARKER)
    assert "\n" not in text
    assert text.split(" ")[1] == "seq=7"


def test_decode_of_garbage_returns_negative_one_and_does_not_raise() -> None:
    garbage = [
        "",
        "not a snapshot at all",
        "RANTI-INDEX-V1 seq=not-a-number written=x count=1 data=[]",
        '{"blob_id": "blob-1", "text": "hello"}',
        INDEX_MARKER + " seq=3 written=now count=1 data={}",
        INDEX_MARKER + " seq=3 written=now count=1 data=[{bad json]",
    ]

    for text in garbage:
        assert decode_snapshot(text) == (-1, [])
        assert is_snapshot(text) is False


def test_select_drops_superseded_and_contradicted_before_active() -> None:
    active = record("active", "a" * 20, importance=0.2)
    superseded = record(
        "superseded",
        "s" * 4000,
        status="superseded",
        importance=0.99,
        superseded_by="active",
    )
    contradicted = record("contradicted", "c" * 4000, status="contradicted", importance=0.95)

    selected = select_snapshot_records([superseded, contradicted, active], 600)

    assert [item.blob_id for item in selected] == ["active"]
    encoded = encode_snapshot(selected, 1, "2026-01-01T00:00:00+00:00")
    assert len(encoded.encode("utf-8")) <= 600


def test_select_keeps_the_highest_importance_active_under_a_byte_budget() -> None:
    low = record("low", "l" * 200, importance=0.1)
    high = record("high", "h" * 200, importance=0.9)

    selected = select_snapshot_records([low, high], 600)

    assert [item.blob_id for item in selected] == ["high"]


def test_an_oversized_snapshot_raises_a_value_error() -> None:
    huge = record("huge", "x" * 70000)

    with pytest.raises(ValueError):
        encode_snapshot([huge], 1, "2026-01-01T00:00:00+00:00")
