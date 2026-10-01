"""Telegram webhook. Parses an Update, calls one use case, always answers 200."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from core.container import Container, get_container
from core.errors import RantiError
from schemas.attachment_schema import AttachmentSchema

logger = logging.getLogger("ranti.router.telegram")

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


def build_router() -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["webhooks"])

    @router.post("/telegram/{secret}")
    async def telegram(
        secret: str, request: Request, container: Container = Depends(get_container)
    ) -> dict[str, object]:
        expected = container.settings.telegram_webhook_secret
        if expected and secret != expected:
            return {"ok": False, "reason": "bad_secret"}

        payload = await request.json()
        if not isinstance(payload, dict):
            return {"ok": True, "handled": False}
        service = container.conversation_service

        try:
            callback = _extract_callback(payload)
            if callback is not None:
                chat_id, display_name, callback_id, callback_data = callback
                # A tap is an interaction, not a turn: this path stores no text.
                await service.handle_callback_query(
                    "telegram", chat_id, display_name, chat_id, callback_id, callback_data
                )
                return {"ok": True, "handled": True, "callback": True}

            document = _extract_document(payload)
            if document is not None:
                chat_id, display_name, attachment = document
                result = await service.handle_surface_attachment(
                    "telegram", chat_id, display_name, chat_id, attachment
                )
                return {
                    "ok": True,
                    "handled": True,
                    "attachment": True,
                    "turn_id": result.turn_id if result is not None else None,
                    "refused": result is None,
                }

            voice = _extract_voice(payload)
            if voice is not None:
                chat_id, display_name, attachment = voice
                result = await service.handle_surface_voice(
                    "telegram", chat_id, display_name, chat_id, attachment
                )
                return {
                    "ok": True,
                    "handled": True,
                    "voice": True,
                    "turn_id": result.turn_id if result is not None else None,
                }

            media = _extract_media(payload)
            if media is not None:
                chat_id, display_name, attachment = media
                await service.handle_surface_attachment(
                    "telegram", chat_id, display_name, chat_id, attachment
                )
                return {"ok": True, "handled": True, "attachment": True, "refused": True}

            parsed = _extract_text(payload)
            if parsed is None:
                return {"ok": True, "handled": False}

            chat_id, display_name, text, _ = parsed
            result = await service.handle_surface_turn(
                surface="telegram",
                surface_user_id=chat_id,
                display_name=display_name,
                text=text,
                recipient_id=chat_id,
            )
            if result is None:
                return {"ok": True, "handled": True, "command": True}
        except RantiError as error:
            # Error mapping is the one branch a router is allowed to own.
            logger.warning("telegram update failed code=%s", error.code)
            recipient = _reply_target(payload)
            if recipient is not None:
                await service.notify_unavailable(recipient)
            return {"ok": True, "handled": True, "degraded": True, "reason": error.code}

        return {
            "ok": True,
            "handled": True,
            "turn_id": result.turn_id,
            "recalled": len(result.recalled),
        }

    return router


def _reply_target(payload: dict[str, Any]) -> str | None:
    """Best-effort recipient for a failure notice, without raising."""
    for extractor in (
        _extract_text,
        _extract_document,
        _extract_voice,
        _extract_media,
        _extract_callback,
    ):
        parsed = extractor(payload)
        if parsed is not None:
            return str(parsed[0])
    return None
