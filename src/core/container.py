"""Composition root. The only place concrete dependencies are constructed."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from core.attachment_parser import AttachmentParser
from core.config import Settings
from core.database import Database
from core.gateways.llm_gateway import (
    LlmGateway,
    build_openai_provider_clients,
    build_provider_boundaries,
)
from core.gateways.memwal_gateway import MemWalGateway
from core.gateways.offline_llm_gateway import OfflineLlm
from core.gateways.telegram_gateway import TelegramGateway
from core.gateways.transcription_gateway import TranscriptionGateway
from core.gateways.web_gateway import WebGateway
from core.reminder_scheduler import ReminderScheduler
from core.resilience import Boundary, ResiliencePolicy, StructuredLogMetricSink
from core.tools.calculate_tool import build_calculator_tool
from core.tools.calendar_tools import build_calendar_tools
from core.tools.capability_tools import build_capability_tools
from core.tools.memory_access import MemoryAccess
from core.tools.memory_tools import build_memory_tools
from core.tools.reminder_store import ReminderStore
from core.tools.reminder_tools import build_reminder_tools
from core.tools.tool_registry import ToolRegistry
from core.tools.web_tools import build_web_tools
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.pairing_crud import PairingCrud
from crud.reminder_crud import ReminderCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from services.conversation_service import ConversationService
from services.memory_admin_service import MemoryAdminService
from services.user_service import UserService

logger = logging.getLogger("ranti.container")


@dataclass(frozen=True)
class Container:
    """Fully wired object graph. No module-level mutable singletons live here."""

    settings: Settings
    database: Database
    memory_gateway: MemWalGateway
    llm_gateway: LlmGateway | OfflineLlm | None
    telegram_gateway: TelegramGateway | None
    user_service: UserService
    conversation_service: ConversationService
    memory_admin_service: MemoryAdminService
    # Added for the agent surface. They carry defaults so an existing test that
    # builds a Container by hand keeps working without naming them.
    tools: ToolRegistry | None = None
    web_gateway: WebGateway | None = None
    transcription_gateway: TranscriptionGateway | None = None
    reminder_scheduler: ReminderScheduler | None = None


def build_memory_boundary(settings: Settings) -> Boundary:
    return Boundary(
        ResiliencePolicy(
            dependency="walrus-memory",
            timeout_seconds=settings.memwal_timeout_seconds,
            failure_threshold=4,
            reset_timeout_seconds=20.0,
            max_attempts=3,
            max_concurrency=4,
            # The relayer is append-only, so an accepted write is never retried.
            retry_writes=False,
        ),
        StructuredLogMetricSink("walrus-memory"),
    )


def build_memory_gateway(settings: Settings) -> MemWalGateway:
    """Use the live relayer when credentials exist, the offline mock otherwise."""
    if settings.memwal_configured:
        from memwal import MemWal

        client: Any = MemWal.create(
            key=settings.memwal_private_key,
            account_id=settings.memwal_account_id,
            server_url=settings.memwal_server_url,
            namespace=settings.memwal_namespace_prefix,
        )
        mode = "walrus"
    else:
        from memwal import MemWalMock

        client = MemWalMock.create(namespace=settings.memwal_namespace_prefix)
        mode = "mock"

    return MemWalGateway(client=client, boundary=build_memory_boundary(settings), mode=mode)


def _boundary_factory(dependency: str, timeout_seconds: float) -> Boundary:
    return Boundary(
        ResiliencePolicy(
            dependency=dependency,
            timeout_seconds=timeout_seconds,
            failure_threshold=3,
            reset_timeout_seconds=15.0,
            max_attempts=2,
            max_concurrency=4,
        ),
        StructuredLogMetricSink(dependency),
    )


def build_llm_gateway(settings: Settings) -> LlmGateway | OfflineLlm:
    """Configured providers win. With none configured, fall back to the offline model.

    The fallback keeps the promise in the README: a fresh clone with no API keys
    still runs a complete turn and still demonstrates consolidation. It is loud
    about being a fallback, so a missing key in production is visible rather than
    silently served by a deterministic stub.
    """
    if not settings.llm_providers:
        logger.warning(
            "no LLM provider is configured; using the deterministic offline model. "
            "Set GROQ_API_KEY or GEMINI_API_KEY for real answers."
        )
        return OfflineLlm()

    from openai import AsyncOpenAI

    boundaries = build_provider_boundaries(settings.llm_providers, _boundary_factory)
    clients = build_openai_provider_clients(settings.llm_providers, AsyncOpenAI)
    return LlmGateway(settings.llm_providers, boundaries, clients)


def build_telegram_gateway(settings: Settings) -> TelegramGateway | None:
    if not settings.telegram_configured:
        return None
    return TelegramGateway(
        token=settings.telegram_bot_token,
        boundary=_boundary_factory("telegram", settings.telegram_timeout_seconds),
    )


def build_web_gateway(settings: Settings) -> WebGateway:
    """One outbound boundary for every web tool: timeout and breaker included."""
    return WebGateway(boundary=_boundary_factory("web", 35.0), timeout_seconds=30.0)


def build_transcription_gateway(settings: Settings) -> TranscriptionGateway | None:
    if not settings.transcription_configured:
        return None
    return TranscriptionGateway(
        api_key=settings.transcription_api_key,
        base_url=settings.transcription_base_url,
        model=settings.transcription_model,
        boundary=_boundary_factory("transcription", settings.transcription_timeout_seconds),
        timeout_seconds=settings.transcription_timeout_seconds,
    )


def build_tool_registry(
    memories: MemoryCrud,
    memory_gateway: MemWalGateway,
    reminders: ReminderCrud,
    web_gateway: WebGateway,
) -> ToolRegistry:
    """Assemble the capability list. A tool is one declaration, registered here."""
    registry = ToolRegistry()
    memory_access = MemoryAccess(memories=memories, gateway=memory_gateway)
    reminder_store = ReminderStore(reminders=reminders)
    for spec in build_memory_tools(memory_access):
        registry.register(spec)
    for spec in build_reminder_tools(reminder_store):
        registry.register(spec)
    for spec in build_web_tools(web_gateway):
        registry.register(spec)
    for spec in build_capability_tools():
        registry.register(spec)
    for spec in build_calendar_tools():
        registry.register(spec)
    registry.register(build_calculator_tool())
    return registry


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    database.migrate()

    users = UserCrud(database)
    memories = MemoryCrud(database)
    turns = TurnCrud(database)
    contradictions = ContradictionCrud(database)
    reminders = ReminderCrud(database)
    pairings = PairingCrud(database)

    memory_gateway = build_memory_gateway(resolved)
    llm_gateway = build_llm_gateway(resolved)
    telegram_gateway = build_telegram_gateway(resolved)
    web_gateway = build_web_gateway(resolved)
    transcription_gateway = build_transcription_gateway(resolved)
    tools = build_tool_registry(memories, memory_gateway, reminders, web_gateway)

    user_service = UserService(users=users, settings=resolved)
    conversation_service = ConversationService(
        users=users,
        memories=memories,
        turns=turns,
        contradictions=contradictions,
        memory_gateway=memory_gateway,
        llm_gateway=llm_gateway,
        settings=resolved,
        # Without this the Telegram surface parses and stores and then never
        # replies. It is wired here rather than inside the service so the
        # service keeps depending on a protocol, not on Telegram.
        reply_channel=telegram_gateway,
        # Same gateway, narrower protocol: the service may download a file but
        # still cannot reach into the Telegram API for anything else.
        attachment_gateway=telegram_gateway,
        attachment_parser=AttachmentParser(),
        # The agent loop runs inside handle_turn, so every surface reaches it.
        tools=tools,
        transcription_gateway=transcription_gateway,
        pairing=pairings,
    )
    memory_admin_service = MemoryAdminService(
        users=users,
        memories=memories,
        turns=turns,
        contradictions=contradictions,
        memory_gateway=memory_gateway,
        settings=resolved,
    )
    reminder_scheduler = ReminderScheduler(
        reminders=reminders,
        reply_channel=telegram_gateway,
    )

    return Container(
        settings=resolved,
        database=database,
        memory_gateway=memory_gateway,
        llm_gateway=llm_gateway,
        telegram_gateway=telegram_gateway,
        user_service=user_service,
        conversation_service=conversation_service,
        memory_admin_service=memory_admin_service,
        tools=tools,
        web_gateway=web_gateway,
        transcription_gateway=transcription_gateway,
        reminder_scheduler=reminder_scheduler,
    )


def get_container(request: Request) -> Container:
    """FastAPI dependency that reads the container from the application state."""
    return request.app.state.container
