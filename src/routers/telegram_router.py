"""Telegram webhook. Parses an Update, claims it once, answers 200 at once.

Telegram considers a webhook delivery failed when it is not acknowledged
quickly, and re-delivers the update. A turn takes tens of seconds, so the work
is handed to the background runner and the route returns immediately. The update
id is claimed in the database first, so a re-delivery is recognized even after a
redeploy and is never processed twice.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from core.container import Container, get_container
from schemas.attachment_schema import AttachmentSchema
from services.conversation_service import ConversationService

logger = logging.getLogger("ranti.router.telegram")

UpdatePlan = tuple[str, Callable[[], Awaitable[None]], Callable[[], Awaitable[None]]]

# Message keys that carry media this project cannot read. The key is passed
# through as the media kind so the service can name it in the refusal.
MEDIA_KEYS = (
    "photo",
    "sticker",
    "animation",
    "voice",
    "audio",
    "video",
    "video_note",
    "contact",
    "location",
    "venue",
    "poll",
    "dice",
)


def _identity(message: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (chat_id, display_name, language) or None when unusable."""
    sender = message.get("from") or {}
    if sender.get("is_bot"):
        return None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    display_name = (
        " ".join(
            part
            for part in (sender.get("first_name"), sender.get("last_name"))
            if isinstance(part, str) and part
        ).strip()
        or (sender.get("username") if isinstance(sender.get("username"), str) else None)
        or f"user {chat_id}"
    )
    language = sender.get("language_code") if isinstance(sender.get("language_code"), str) else ""
    return str(chat_id), display_name, language


def _message(payload: dict[str, Any]) -> dict[str, Any] | None:
    message = payload.get("message") or payload.get("edited_message")
    return message if isinstance(message, dict) else None


def _extract_text(payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return (chat_id, display_name, text, language) or None when not a text message."""
    message = _message(payload)
    if message is None:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    identity = _identity(message)
    if identity is None:
        return None
    chat_id, display_name, language = identity
    return chat_id, display_name, text, language


def _extract_document(payload: dict[str, Any]) -> tuple[str, str, AttachmentSchema] | None:
    """Return (chat_id, display_name, attachment) when the update carries a file."""
    message = _message(payload)
    if message is None:
        return None
    document = message.get("document")
    if not isinstance(document, dict):
        return None
    identity = _identity(message)
    if identity is None:
        return None
    chat_id, display_name, _ = identity
    raw_size = document.get("file_size")
    size = raw_size if isinstance(raw_size, int) else None
    attachment = AttachmentSchema(
        media_kind="document",
        file_id=str(document.get("file_id", "")),
        file_name=str(document.get("file_name", "") or ""),
        mime_type=str(document.get("mime_type", "") or ""),
        file_size=size,
        caption=str(message.get("caption", "") or ""),
    )
    return chat_id, display_name, attachment


def _extract_voice(payload: dict[str, Any]) -> tuple[str, str, AttachmentSchema] | None:
    """Return (chat_id, display_name, attachment) when the update is a voice note.

    A voice note is handled before the generic media refusal: it is media we can
    actually read, by transcribing it.
    """
    message = _message(payload)
    if message is None:
        return None
    voice = message.get("voice")
    if not isinstance(voice, dict):
        return None
    identity = _identity(message)
    if identity is None:
        return None
    chat_id, display_name, _ = identity
    raw_size = voice.get("file_size")
    size = raw_size if isinstance(raw_size, int) else None
    attachment = AttachmentSchema(
        media_kind="voice",
        file_id=str(voice.get("file_id", "")),
        file_name="",
        mime_type=str(voice.get("mime_type", "") or "audio/ogg"),
        file_size=size,
        caption=str(message.get("caption", "") or ""),
    )
    return chat_id, display_name, attachment


def _extract_media(payload: dict[str, Any]) -> tuple[str, str, AttachmentSchema] | None:
    """Return an attachment description for media we cannot read, or None."""
    message = _message(payload)
    if message is None:
        return None
    media_kind = next(
        (key for key in MEDIA_KEYS if isinstance(message.get(key), (dict, list))), None
    )
    if media_kind is None:
        return None
    identity = _identity(message)
    if identity is None:
        return None
    chat_id, display_name, _ = identity
    caption = message.get("caption")
    return (
        chat_id,
        display_name,
        AttachmentSchema(
            media_kind=media_kind,
            caption=str(caption) if isinstance(caption, str) else "",
        ),
    )


def _extract_callback(
    payload: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    """Return (chat_id, display_name, callback_query_id, callback_data) or None."""
    callback = payload.get("callback_query")
    if not isinstance(callback, dict):
        return None
    sender = callback.get("from") or {}
    if sender.get("is_bot"):
        return None
    message = callback.get("message") or {}
    chat = (message.get("chat") if isinstance(message, dict) else None) or {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is None:
        chat_id = sender.get("id")
    if chat_id is None:
        return None
    display_name = (
        " ".join(
            part
            for part in (sender.get("first_name"), sender.get("last_name"))
            if isinstance(part, str) and part
        ).strip()
        or (sender.get("username") if isinstance(sender.get("username"), str) else None)
        or f"user {chat_id}"
    )
    return (
        str(chat_id),
        display_name,
        str(callback.get("id", "")),
        str(callback.get("data", "")),
    )


def _failure_notice(
    service: ConversationService, chat_id: str
) -> Callable[[], Awaitable[None]]:
    """An honest notice for a background turn that could not be served."""

    async def notify() -> None:
        await service.notify_unavailable(chat_id)

    return notify


def _plan(payload: dict[str, Any], service: ConversationService) -> UpdatePlan | None:
    """Describe the work one update needs, or None when it carries nothing.

    Parsing stays in the router; the returned closures call exactly one use case
    each, so the background runner owns execution but not transport knowledge.
    """
    callback = _extract_callback(payload)
    if callback is not None:
        chat_id, display_name, callback_id, callback_data = callback

        async def run_callback() -> None:
            await service.handle_callback_query(
                "telegram", chat_id, display_name, chat_id, callback_id, callback_data
            )

        return chat_id, run_callback, _failure_notice(service, chat_id)

    document = _extract_document(payload)
    if document is not None:
        chat_id, display_name, attachment = document

        async def run_document() -> None:
            await service.handle_surface_attachment(
                "telegram", chat_id, display_name, chat_id, attachment
            )

        return chat_id, run_document, _failure_notice(service, chat_id)

    voice = _extract_voice(payload)
    if voice is not None:
        chat_id, display_name, attachment = voice

        async def run_voice() -> None:
            await service.handle_surface_voice(
                "telegram", chat_id, display_name, chat_id, attachment
            )

        return chat_id, run_voice, _failure_notice(service, chat_id)

    media = _extract_media(payload)
    if media is not None:
        chat_id, display_name, attachment = media

        async def run_media() -> None:
            await service.handle_surface_attachment(
                "telegram", chat_id, display_name, chat_id, attachment
            )

        return chat_id, run_media, _failure_notice(service, chat_id)

    parsed = _extract_text(payload)
    if parsed is None:
        return None
    chat_id, display_name, text, _ = parsed

    async def run_text() -> None:
        await service.handle_surface_turn(
            surface="telegram",
            surface_user_id=chat_id,
            display_name=display_name,
            text=text,
            recipient_id=chat_id,
        )

    return chat_id, run_text, _failure_notice(service, chat_id)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["webhooks"])

    @router.post("/telegram/{secret}")
    async def telegram(
        secret: str,
        request: Request,
        background: BackgroundTasks,
        container: Container = Depends(get_container),
    ) -> dict[str, object]:
        expected = container.settings.telegram_webhook_secret
        if expected and secret != expected:
            return {"ok": False, "reason": "bad_secret"}

        payload = await request.json()
        if not isinstance(payload, dict):
            return {"ok": True, "handled": False}

        update_id = payload.get("update_id")
        if update_id is not None and not container.telegram_runner.claim(
            str(update_id)
        ):
            # Telegram re-delivered an update that was already accepted. Another
            # 200 stops the retries; the turn is not run a second time.
            logger.info("telegram update duplicate update_id=%s", update_id)
            return {"ok": True, "handled": True, "duplicate": True}

        service = container.conversation_service
        plan = _plan(payload, service)
        if plan is None:
            return {"ok": True, "handled": False}

        key, work, on_failure = plan
        # The turn runs after the response is sent. The route never awaits it,
        # so Telegram sees its 200 long before a slow model or memory call ends.
        background.add_task(container.telegram_runner.run, key, work, on_failure)
        return {"ok": True, "handled": True, "accepted": True}

    return router
