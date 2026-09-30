"""Transport DTOs for the memory boundary. Wire shapes only, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecalledMemorySchema:
    """One memory returned by a recall call."""

    blob_id: str
    text: str
    distance: float


@dataclass(frozen=True)
class RecallOutcomeSchema:
    """Recall result that states whether the answer is complete.

    An empty ``memories`` tuple with ``degraded=True`` means "we could not ask",
    which the caller must never present as "you have no memories".
    """

    memories: tuple[RecalledMemorySchema, ...]
    degraded: bool = False
    error: str | None = None


@dataclass(frozen=True)
class NamespaceListingSchema:
    """Namespace listing that states whether it is complete."""

    namespaces: tuple[NamespaceSummarySchema, ...]
    degraded: bool = False
    error: str | None = None


@dataclass(frozen=True)
class StoredMemorySchema:
    """A memory acknowledged as persisted by the relayer."""

    blob_id: str
    namespace: str
    owner: str


@dataclass(frozen=True)
class ExtractedFactSchema:
    """One fact extracted from a conversation turn."""

    text: str
    blob_id: str | None


@dataclass(frozen=True)
class NamespaceSummarySchema:
    """One namespace row, used for the evidence dashboard."""

    name: str
    memory_count: int
    storage_used: int
    updated_at: str


@dataclass(frozen=True)
class RestoreReportSchema:
    """Outcome of a restore call, used by the passport recovery path."""

    namespace: str
    restored: int
    skipped: int
    failed: int
    total: int
    truncated: bool


@dataclass(frozen=True)
class MemoryHealthSchema:
    """Relayer health, surfaced on the health endpoint."""

    status: str
    version: str
    write_ready: bool
