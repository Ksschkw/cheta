"""Speech to text for inbound voice notes.

Uses Groq's OpenAI-compatible audio transcription endpoint with Whisper on the
free tier, through the same boundary pattern as every other outbound call. The
gateway never invents a transcript: an empty answer or a failed call is a typed
failure, and the caller tells the person it could not transcribe.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.errors import DependencyUnavailableError
from core.resilience import Boundary, failure_fallback

logger = logging.getLogger("ranti.gateway.transcription")

# Telegram's bot download ceiling is 20 MB, so nothing larger can arrive anyway.
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class TranscriptionGateway:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        boundary: Boundary,
        client_factory: Callable[..., Any] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._boundary = boundary
        self._client_factory = client_factory
        self._timeout = timeout_seconds

    def _client(self) -> Any:
        factory = self._client_factory
        if factory is None:
            import httpx

            factory = httpx.AsyncClient
        return factory(timeout=self._timeout)

    async def transcribe(self, filename: str, content: bytes, mime_type: str) -> str:
        if not content:
            raise DependencyUnavailableError("transcription", "the audio was empty")
        if len(content) > MAX_AUDIO_BYTES:
            raise DependencyUnavailableError(
                "transcription", "the audio is larger than the 20 MB limit"
            )
        name = filename.strip() or "voice.ogg"
        kind = mime_type.strip() or "audio/ogg"

        async def operation() -> str:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files={"file": (name, content, kind)},
                    data={"model": self._model},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict):
                return ""
            return str(body.get("text") or "")

        outcome = await self._boundary.call(
            operation,
            failure_fallback("transcription", "transcribe_failed"),
            idempotent=True,
        )
        if not outcome.ok or outcome.value is None:
            raise DependencyUnavailableError(
                "transcription", outcome.error or "the audio could not be transcribed"
            )
        text = outcome.value.strip()
        if not text:
            # Never claim a transcript that does not exist.
            raise DependencyUnavailableError("transcription", "the audio contained no speech")
        return text
