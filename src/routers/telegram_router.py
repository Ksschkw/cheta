"""Telegram webhook. Parses an Update, calls one use case, always answers 200."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from core.container import Container, get_container
from core.errors import RantiError

logger = logging.getLogger("ranti.router.telegram")


def _extract(payload: dict) -> tuple[str, str, str, str] | None:
    """Return (chat_id, display_name, text, language) or None when not a text message."""
    message = payload.get("message") or payload.get("edited_message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from") or {}
    if sender.get("is_bot"):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
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
    return str(chat_id), display_name, text, language


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
        parsed = _extract(payload if isinstance(payload, dict) else {})
        if parsed is None:
            return {"ok": True, "handled": False}

        chat_id, display_name, text, _ = parsed
        try:
            result = await container.conversation_service.handle_surface_turn(
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
            logger.warning("telegram turn failed code=%s", error.code)
            await container.conversation_service.notify_unavailable(chat_id)
            return {"ok": True, "handled": True, "degraded": True, "reason": error.code}

        return {
            "ok": True,
            "handled": True,
            "turn_id": result.turn_id,
            "recalled": len(result.recalled),
        }

    return router
