"""The composition root must wire the Telegram reply channel.

A real defect motivated this file: the Telegram gateway was built, stored on the
container, and never handed to the conversation service, so every Telegram turn
parsed the message, wrote memories, and then silently never replied. Nothing in
the in-process webhook tests caught it, because those tests inject their own
recording reply channel. This test covers the wiring itself.
"""

from __future__ import annotations

from core.config import Settings
from core.container import build_container
from core.gateways.telegram_gateway import TelegramGateway


def configured_settings() -> Settings:
    return Settings(
        database_path=":memory:",
        memwal_namespace_prefix="ranti",
        telegram_bot_token="123456:test-token",
        telegram_webhook_secret="secret",
        public_base_url="https://example.invalid",
    )


def test_the_telegram_gateway_is_built_when_a_token_is_configured() -> None:
    container = build_container(configured_settings())

    assert isinstance(container.telegram_gateway, TelegramGateway)


def test_the_conversation_service_can_actually_reply_on_telegram() -> None:
    """The regression: the gateway existed but was never injected."""
    container = build_container(configured_settings())

    assert container.conversation_service._reply_channel is container.telegram_gateway


def test_no_reply_channel_is_wired_when_telegram_is_not_configured() -> None:
    container = build_container(Settings(database_path=":memory:"))

    assert container.telegram_gateway is None
    assert container.conversation_service._reply_channel is None


def test_the_agent_tools_are_wired_into_the_service_that_every_surface_uses() -> None:
    """Every surface reaches handle_turn, so the registry must be wired there."""
    container = build_container(Settings(database_path=":memory:"))

    assert container.tools is not None
    assert container.conversation_service._tools is container.tools
    names = container.tools.names()
    for expected in (
        "memory_recall",
        "reminder_set",
        "web_search",
        "crawl",
        "wikipedia",
        "weather",
        "calculate",
        "fetch_url",
        "document_question",
    ):
        assert expected in names


def test_the_reminder_scheduler_is_wired_to_the_telegram_channel() -> None:
    container = build_container(configured_settings())

    assert container.reminder_scheduler is not None
    assert container.reminder_scheduler._reply_channel is container.telegram_gateway


def test_transcription_is_absent_without_a_key_and_present_with_one() -> None:
    without = build_container(Settings(database_path=":memory:"))
    assert without.transcription_gateway is None

    with_key = build_container(
        Settings(database_path=":memory:", transcription_api_key="test-key")
    )
    assert with_key.transcription_gateway is not None
    assert with_key.conversation_service._transcription_gateway is with_key.transcription_gateway
