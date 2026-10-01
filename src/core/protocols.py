"""Interfaces the service layer depends on. Implementations are injected.

Nothing here imports a driver, a framework, or a concrete gateway.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from schemas.llm_schema import ChatMessageSchema, CompletionSchema
from schemas.memory_schema import (
    AcceptedMemorySchema,
    ExtractedFactSchema,
    MemoryHealthSchema,
    NamespaceListingSchema,
    RecallOutcomeSchema,
    RestoreReportSchema,
    StoredMemorySchema,
)


class MemoryGatewayProtocol(Protocol):
    """Walrus Memory, wrapped in an outbound boundary."""

    async def health(self) -> MemoryHealthSchema: ...

    async def remember(
        self, text: str, namespace: str, idempotency_key: str | None = None
    ) -> StoredMemorySchema: ...

    async def remember_accepted(
        self, text: str, namespace: str, idempotency_key: str | None = None
    ) -> AcceptedMemorySchema: ...

    async def wait_for_remember(self, job_id: str) -> StoredMemorySchema: ...

    async def analyze(
        self, text: str, namespace: str, occurred_at: str | None = None
    ) -> list[ExtractedFactSchema]: ...

    async def recall(
        self,
        query: str,
        namespace: str,
        limit: int = 25,
        max_distance: float | None = None,
    ) -> RecallOutcomeSchema: ...

    async def embed(self, text: str) -> Sequence[float]: ...

    async def list_namespaces(self, limit: int = 100) -> NamespaceListingSchema: ...

    async def restore(self, namespace: str, limit: int = 50) -> RestoreReportSchema: ...

    @property
    def degraded(self) -> bool: ...


class LlmGatewayProtocol(Protocol):
    """The configured model, with provider failover handled inside the gateway."""

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema: ...


class TelegramGatewayProtocol(Protocol):
    """The Telegram Bot API, wrapped in an outbound boundary."""

    async def send_message(self, chat_id: str, text: str) -> None: ...

    async def set_webhook(self, url: str, secret_token: str) -> None: ...


class ReplyChannelProtocol(Protocol):
    """A push transport the conversation can deliver its own answer on."""

    async def send_message(self, recipient_id: str, text: str) -> None: ...

    async def send_typing(self, recipient_id: str) -> None: ...
