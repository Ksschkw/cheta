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
from schemas.web_schema import FetchedPageSchema, SearchResultSchema


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

    async def send_message(
        self, chat_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> None: ...

    async def send_document(
        self,
        chat_id: str,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None: ...

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None: ...

    async def get_file_path(self, file_id: str) -> str: ...

    async def download_file(self, file_path: str) -> bytes: ...

    async def set_webhook(self, url: str, secret_token: str) -> None: ...


class AttachmentGatewayProtocol(Protocol):
    """Downloads an inbound file. Split out so a service never imports Telegram."""

    async def get_file_path(self, file_id: str) -> str: ...

    async def download_file(self, file_path: str) -> bytes: ...


class AttachmentParserProtocol(Protocol):
    """Turns one supported document's bytes into plain text, locally."""

    def classify(self, file_name: str, mime_type: str) -> str | None: ...

    def extract(self, kind: str, file_name: str, content: bytes) -> str: ...


class WebGatewayProtocol(Protocol):
    """Bounded, SSRF-guarded outbound web access for the network tools."""

    async def fetch_page(self, url: str, max_bytes: int) -> FetchedPageSchema: ...

    async def fetch_robots(self, origin: str) -> str | None: ...

    async def search(self, query: str, limit: int) -> list[SearchResultSchema]: ...

    def validate_url(self, url: str) -> None: ...


class TranscriptionGatewayProtocol(Protocol):
    """Speech-to-text for an inbound voice note, wrapped in a boundary."""

    async def transcribe(self, filename: str, content: bytes, mime_type: str) -> str: ...


class ReplyChannelProtocol(Protocol):
    """A push transport the conversation can deliver its own answer on."""

    async def send_message(
        self, recipient_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> None: ...

    async def send_document(
        self,
        recipient_id: str,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None: ...

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None: ...

    async def send_typing(self, recipient_id: str) -> None: ...

    async def send_photo(
        self,
        recipient_id: str,
        image_path: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> None: ...
