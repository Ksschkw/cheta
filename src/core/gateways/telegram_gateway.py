"""Telegram Bot API gateway. One outbound boundary, no SDK dependency."""

from __future__ import annotations

import json
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

    async def _post(
        self,
        method: str,
        payload: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        async with self._client_factory(timeout=10.0) as client:
            if files is None:
                response = await client.post(f"{self._base_url}/{method}", json=payload)
            else:
                response = await client.post(
                    f"{self._base_url}/{method}", data=payload, files=files
                )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok", False):
                raise DependencyUnavailableError("telegram", str(body.get("description", "error")))
            return body

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        chunks = split_message(text)
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == last else None

            async def operation(
                chunk: str = chunk, markup: dict[str, object] | None = markup
            ) -> dict[str, Any]:
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                if markup is not None:
                    payload["reply_markup"] = markup
                return await self._post("sendMessage", payload)

            outcome = await self._boundary.call(
                operation,
                failure_fallback("telegram", "send_failed"),
                idempotent=False,
            )
            if not outcome.ok:
                raise DependencyUnavailableError(
                    "telegram", outcome.error or "could not deliver message"
                )

    async def send_document(
        self,
        chat_id: str,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        """Upload bytes as a document. Used for the memory passport export."""
        payload: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        files = {"document": (filename, content, "application/octet-stream")}

        async def operation() -> dict[str, Any]:
            return await self._post("sendDocument", payload, files=files)

        outcome = await self._boundary.call(
            operation,
            failure_fallback("telegram", "send_document_failed"),
            idempotent=False,
        )
        if not outcome.ok:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not deliver document"
            )

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None:
        """Stop the spinner on a tapped inline button.

        Cosmetic in the sense that the queue already believes the tap arrived,
        but a button that spins forever reads as a frozen bot, so every callback
        path calls this even when the action itself fails.
        """
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        async def operation() -> dict[str, Any]:
            return await self._post("answerCallbackQuery", payload)

        try:
            await self._boundary.call(
                operation,
                failure_fallback("telegram", "answer_callback_failed"),
                idempotent=True,
            )
        except Exception as error:  # noqa: BLE001 - never break a tap for this
            logger.debug("answerCallbackQuery failed: %s", type(error).__name__)

    async def get_file_path(self, file_id: str) -> str:
        """Resolve a Telegram file_id to a temporary download path."""

        async def operation() -> str:
            body = await self._post("getFile", {"file_id": file_id})
            result = body.get("result") or {}
            path = result.get("file_path")
            if not isinstance(path, str) or not path:
                raise DependencyUnavailableError("telegram", "getFile returned no file_path")
            return path

        outcome = await self._boundary.call(
            operation, failure_fallback("telegram", "get_file_failed"), idempotent=True
        )
        if not outcome.ok or outcome.value is None:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not resolve the file"
            )
        return outcome.value

    async def download_file(self, file_path: str) -> bytes:
        """Fetch the bytes of a file previously resolved with get_file_path."""
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"

        async def operation() -> bytes:
            async with self._client_factory(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content

        outcome = await self._boundary.call(
            operation, failure_fallback("telegram", "download_failed"), idempotent=True
        )
        if not outcome.ok or outcome.value is None:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not download the file"
            )
        return outcome.value

    async def send_photo(
        self,
        recipient_id: str,
        image_path: str,
        caption: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Upload a local image. Telegram can only receive photos as a file
        upload, so this uses multipart rather than the JSON helper."""
        from pathlib import Path as _Path

        path = _Path(image_path)
        if not path.is_file():
            raise DependencyUnavailableError("telegram", f"missing image {image_path}")

        async def operation() -> dict[str, Any]:
            with path.open("rb") as handle:
                files = {"photo": (path.name, handle, "image/png")}
                data = {"chat_id": recipient_id}
                if caption:
                    data["caption"] = caption[:1024]
                if reply_markup is not None:
                    # sendPhoto takes the keyboard as a JSON string in multipart.
                    data["reply_markup"] = json.dumps(reply_markup)
                async with self._client_factory(timeout=30.0) as client:
                    response = await client.post(
                        f"{self._base_url}/sendPhoto", data=data, files=files
                    )
                    response.raise_for_status()
                    body = response.json()
            if not body.get("ok", False):
                raise DependencyUnavailableError(
                    "telegram", str(body.get("description", "sendPhoto failed"))
                )
            return body

        outcome = await self._boundary.call(
            operation, failure_fallback("telegram", "send_photo_failed"), idempotent=False
        )
        if not outcome.ok:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not deliver photo"
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
                    "allowed_updates": ["message", "callback_query"],
                },
            )

        outcome = await self._boundary.call(
            operation, failure_fallback("telegram", "set_webhook_failed"), idempotent=True
        )
        if not outcome.ok:
            raise DependencyUnavailableError(
                "telegram", outcome.error or "could not register webhook"
            )
