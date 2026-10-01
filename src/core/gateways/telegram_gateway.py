"""Telegram Bot API gateway. One outbound boundary, no SDK dependency."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from core.errors import DependencyUnavailableError
from core.resilience import Boundary, failure_fallback

logger = logging.getLogger("ranti.gateway.telegram")

TELEGRAM_TEXT_LIMIT = 4096
SAFE_CHUNK = 3800


def split_message(text: str, limit: int = SAFE_CHUNK) -> list[str]:
    """Split on paragraph then line boundaries so no message exceeds the API limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramGateway:
    def __init__(
        self,
        token: str,
        boundary: Boundary,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._token = token
        self._boundary = boundary
        self._client_factory = client_factory or httpx.AsyncClient
        self._base_url = f"https://api.telegram.org/bot{token}"

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client_factory(timeout=10.0) as client:
            response = await client.post(f"{self._base_url}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok", False):
                raise DependencyUnavailableError("telegram", str(body.get("description", "error")))
            return body

    async def send_message(self, chat_id: str, text: str) -> None:
        for chunk in split_message(text):
            async def operation(chunk: str = chunk) -> dict[str, Any]:
                return await self._post(
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                )

            outcome = await self._boundary.call(
                operation,
                failure_fallback("telegram", "send_failed"),
                idempotent=False,
            )
            if not outcome.ok:
                raise DependencyUnavailableError(
                    "telegram", outcome.error or "could not deliver message"
                )

    async def send_typing(self, recipient_id: str) -> None:
        """Show a typing indicator. Cosmetic, so a failure is logged, not raised.

        Turns take several seconds because extraction and adjudication are model
        calls, and real users read the silence as a frozen bot.
        """

        async def operation() -> dict[str, Any]:
            return await self._post(
                "sendChatAction", {"chat_id": recipient_id, "action": "typing"}
            )

        try:
            await self._boundary.call(
                operation, failure_fallback("telegram", "typing_failed"), idempotent=True
            )
        except Exception as error:  # noqa: BLE001 - never break a turn for this
            logger.debug("typing indicator failed: %s", type(error).__name__)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        async def operation() -> dict[str, Any]:
            return await self._post(
                "setWebhook",
                {
                    "url": url,
                    "secret_token": secret_token,
                    "allowed_updates": ["message"],
                },
            )

        outcome = await self._boundary.call(
            operation, failure_fallback("telegram", "set_webhook_failed"), idempotent=True
        )
        if not outcome.ok:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not register webhook"
            )
