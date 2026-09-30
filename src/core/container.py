"""Composition root. The only place concrete dependencies are constructed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from core.config import Settings
from core.database import Database
from core.gateways.llm_gateway import (
    LlmGateway,
    build_openai_provider_clients,
    build_provider_boundaries,
)
from core.gateways.memwal_gateway import MemWalGateway
from core.gateways.telegram_gateway import TelegramGateway
from core.resilience import Boundary, ResiliencePolicy, StructuredLogMetricSink
from crud.user_crud import UserCrud
from services.user_service import UserService


@dataclass(frozen=True)
class Container:
    """Fully wired object graph. No module-level mutable singletons live here."""

    settings: Settings
    database: Database
    memory_gateway: MemWalGateway
    llm_gateway: LlmGateway | None
    telegram_gateway: TelegramGateway | None
    user_service: UserService


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


def build_llm_gateway(settings: Settings) -> LlmGateway | None:
    if not settings.llm_providers:
        return None
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


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    database.migrate()

    users = UserCrud(database)
    user_service = UserService(users=users, settings=resolved)
    memory_gateway = build_memory_gateway(resolved)
    llm_gateway = build_llm_gateway(resolved)
    telegram_gateway = build_telegram_gateway(resolved)

    return Container(
        settings=resolved,
        database=database,
        memory_gateway=memory_gateway,
        llm_gateway=llm_gateway,
        telegram_gateway=telegram_gateway,
        user_service=user_service,
    )


def get_container(request: Request) -> Container:
    """FastAPI dependency that reads the container from the application state."""
    return request.app.state.container
