"""Self-describing snapshot of the local memory index.

Walrus Memory can answer "what is close to this query" but never "list
everything", and the Memory Read API needs a Console credential this project
does not hold. A wiped install therefore cannot rebuild its SQLite index by
enumeration. The fix is to persist the index itself as ordinary memories in a
companion namespace: the newest snapshot is recallable with one fixed query and
carries enough metadata to restore status, importance and provenance exactly.

The format is a single ASCII line:

    RANTI-INDEX-V1 seq=<n> written=<iso> count=<n> data=<compact json array>

This module is deliberately free of imports from this project so the wire
format can be reasoned about, versioned and tested on its own.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

INDEX_MARKER = "RANTI-INDEX-V1"
# The recall query. Every snapshot starts with the marker, so the mock's
# token-overlap distance is zero for snapshots and high for ordinary facts.
INDEX_QUERY = "RANTI-INDEX-V1"
# Walrus Memory caps a single remembered item at 64 KiB; leave a little room.
MAX_SNAPSHOT_BYTES = 60000

_SNAPSHOT_PATTERN = re.compile(
    re.escape(INDEX_MARKER) + r" seq=(-?\d+) written=(\S+) count=(\d+) data=(\[.*\])\s*$",
    re.DOTALL,
)


@dataclass(frozen=True)
class IndexedMemoryRecord:
    """One row of the local index, in the shape the snapshot carries it."""

    blob_id: str
    text: str
    status: str
    importance: float
    origin_surface: str
    superseded_by: str | None
    occurred_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "blob_id": self.blob_id,
            "text": self.text,
            "status": self.status,
            "importance": self.importance,
            "origin_surface": self.origin_surface,
            "superseded_by": self.superseded_by,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> IndexedMemoryRecord:
        raw_superseded = payload.get("superseded_by")
        return cls(
            blob_id=str(payload["blob_id"]),
            text=str(payload["text"]),
            status=str(payload["status"]),
            importance=float(payload["importance"]),
            origin_surface=str(payload["origin_surface"]),
            superseded_by=str(raw_superseded) if raw_superseded is not None else None,
            occurred_at=str(payload["occurred_at"]),
        )


def _payload(records: Sequence[IndexedMemoryRecord]) -> str:
    return json.dumps(
        [record.to_dict() for record in records],
        separators=(",", ":"),
        sort_keys=True,
    )


def _compose(sequence: int, written_at: str, records_json: str, count: int) -> str:
    return (
        f"{INDEX_MARKER} seq={sequence} written={written_at} "
        f"count={count} data={records_json}"
    )


def _encoded_size(records: Sequence[IndexedMemoryRecord]) -> int:
    # Over-estimate the seq/count/written widths so a candidate that fits this
    # estimate is guaranteed to fit the real header.
    header = (
        f"{INDEX_MARKER} seq=00000000000000000000 "
        f"written=2000-01-01T00:00:00+00:00 count={len(records):010d} data="
    )
    return len((header + _payload(records)).encode("utf-8"))


def encode_snapshot(
    records: Sequence[IndexedMemoryRecord], sequence: int, written_at: str
) -> str:
    """Serialise records into one single-line snapshot.

    Raises ``ValueError`` when the result would exceed the Walrus Memory item
    cap. Callers must trim with :func:`select_snapshot_records` first.
    """
    ordered = list(records)
    text = _compose(sequence, written_at, _payload(ordered), len(ordered))
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > MAX_SNAPSHOT_BYTES:
        raise ValueError(
            f"index snapshot is {encoded_size} bytes, over the "
            f"{MAX_SNAPSHOT_BYTES}-byte Walrus Memory limit; trim records first"
        )
    return text


def select_snapshot_records(
    records: Sequence[IndexedMemoryRecord], max_bytes: int = MAX_SNAPSHOT_BYTES
) -> list[IndexedMemoryRecord]:
    """Trim a record set to fit the byte budget without losing live facts first.

    Active memories are kept in importance order. Superseded and contradicted
    rows are only allowed in once every active row fits, so a stale row can
    never displace a live one when the budget is tight.
    """
    ordered = list(records)
    active = sorted(
        (record for record in ordered if record.status == "active"),
        key=lambda record: record.importance,
        reverse=True,
    )
    inactive = sorted(
        (record for record in ordered if record.status != "active"),
        key=lambda record: record.importance,
        reverse=True,
    )

    selected: list[IndexedMemoryRecord] = []
    for record in active:
        candidate = selected + [record]
        if _encoded_size(candidate) <= max_bytes:
            selected.append(record)

    # If any active row did not fit, no inactive row may take its place.
    if len(selected) < len(active):
        return selected

    for record in inactive:
        candidate = selected + [record]
        if _encoded_size(candidate) <= max_bytes:
            selected.append(record)
    return selected


def decode_snapshot(text: str) -> tuple[int, list[IndexedMemoryRecord]]:
    """Parse a snapshot, returning ``(-1, [])`` for anything else.

    Recall returns unrelated hits beside snapshots, so this must never raise on
    foreign text. Malformed individual rows are skipped rather than discarding
    an otherwise usable snapshot.
    """
    if not isinstance(text, str):
        return -1, []
    match = _SNAPSHOT_PATTERN.match(text.strip())
    if match is None:
        return -1, []

    try:
        sequence = int(match.group(1))
        payload = json.loads(match.group(4))
    except (TypeError, ValueError, json.JSONDecodeError):
        return -1, []
    if not isinstance(payload, list):
        return -1, []

    records: list[IndexedMemoryRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            records.append(IndexedMemoryRecord.from_dict(item))
        except (KeyError, TypeError, ValueError):
            continue
    return sequence, records


def is_snapshot(text: str) -> bool:
    """True when ``text`` decodes as a snapshot envelope."""
    return decode_snapshot(text)[0] >= 0
