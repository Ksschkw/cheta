"""The memory operations the assistant can perform on its own store.

This is one adapter over the existing ``MemoryCrud`` and memory gateway, so the
agent gains recall, remember and forget without a second memory system. The
forget rule is the same rule ``/forget`` already uses: Walrus Memory cannot
erase a blob, so a note is marked retired and stops surfacing.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from core.errors import DependencyUnavailableError
from core.protocols import MemoryGatewayProtocol
from crud.memory_crud import MemoryCrud
from models.entities.memory_model import STATUS_ACTIVE, STATUS_SUPERSEDED

RECALL_CANDIDATE_LIMIT = 40
DEFAULT_RECALL_LIMIT = 5
MAX_RECALL_LIMIT = 10
REMEMBER_IMPORTANCE = 0.7


class MemoryAccessProtocol(Protocol):
    async def recall(
        self, user_id: str, namespace: str, query: str, limit: int
    ) -> list[str]: ...

    async def remember(
        self, user_id: str, namespace: str, surface: str, text: str
    ) -> str: ...

    def forget(self, user_id: str, index: int) -> str: ...


class MemoryAccess:
    """Recall, remember and forget, on top of the existing store."""

    def __init__(self, memories: MemoryCrud, gateway: MemoryGatewayProtocol) -> None:
        self._memories = memories
        self._gateway = gateway

    def _ordered(self, user_id: str) -> list:
        """The same order /memories and /forget use, so an index agrees."""
        return sorted(
            self._memories.list_for_user(user_id, None, 500),
            key=lambda record: record.importance,
            reverse=True,
        )

    async def recall(
        self, user_id: str, namespace: str, query: str, limit: int
    ) -> list[str]:
        outcome = await self._gateway.recall(query, namespace, limit=RECALL_CANDIDATE_LIMIT)
        if outcome.degraded:
            return ["memory is briefly unavailable, so I could not search what is stored"]
        by_text = {record.text: index for index, record in enumerate(self._ordered(user_id), 1)}
        lines: list[str] = []
        for hit in outcome.memories[:limit]:
            index = by_text.get(hit.text)
            label = f"{index}." if index is not None else "-"
            lines.append(f"{label} {hit.text}")
        return lines

    async def remember(
        self, user_id: str, namespace: str, surface: str, text: str
    ) -> str:
        cleaned = " ".join(text.split())
        if not cleaned:
            return "there was nothing to store"
        existing = self._memories.list_for_user(user_id, None, 500)
        if any(record.text.strip().lower() == cleaned.lower() for record in existing):
            return "that is already stored, so I did not store it again"
        try:
            stored = await self._gateway.remember(
                cleaned, namespace, idempotency_key=self._idempotency_key(user_id, cleaned)
            )
        except DependencyUnavailableError:
            return "the memory store is briefly unavailable, so the note was not stored"
        self._memories.create(
            user_id=user_id,
            blob_id=stored.blob_id,
            namespace=namespace,
            text=cleaned,
            importance=REMEMBER_IMPORTANCE,
            origin_surface=surface,
            occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        return "stored"

    def forget(self, user_id: str, index: int) -> str:
        ordered = self._ordered(user_id)
        if index < 1 or index > len(ordered):
            return f"there is no note {index} to forget"
        target = ordered[index - 1]
        self._memories.mark_status(target.id, STATUS_SUPERSEDED, "user-retracted")
        return (
            f"retired the note \"{target.text}\". It stays on Walrus until its "
            "storage expires, because Walrus Memory cannot erase a blob."
        )

    def count_active(self, user_id: str) -> int:
        return self._memories.count_for_user(user_id, STATUS_ACTIVE)

    @staticmethod
    def _idempotency_key(user_id: str, text: str) -> str:
        """Same 30-minute bucket rule the turn path uses, so a retry cannot duplicate."""
        bucket = int(datetime.now(UTC).timestamp() // 1800)
        return hashlib.sha256(f"{user_id}|tool|{text}|{bucket}".encode()).hexdigest()
