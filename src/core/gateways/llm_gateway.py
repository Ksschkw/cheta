"""Language-model gateway with an explicit provider failover chain.

Each provider is its own outbound dependency and therefore gets its own circuit
breaker. A provider that is open is skipped immediately rather than waited on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from core.config import LlmProviderConfig
from core.errors import DependencyUnavailableError
from core.resilience import Boundary, failure_fallback
from schemas.llm_schema import ChatMessageSchema, CompletionSchema

logger = logging.getLogger("ranti.gateway.llm")


class ProviderClientProtocol(Protocol):
    """Minimal AsyncOpenAI surface this gateway needs."""

    async def complete(
        self, model: str, messages: Sequence[dict[str, str]], temperature: float, max_tokens: int
    ) -> str: ...


class LlmGateway:
    def __init__(
        self,
        providers: Sequence[LlmProviderConfig],
        boundaries: dict[str, Boundary],
        clients: dict[str, ProviderClientProtocol],
    ) -> None:
        if not providers:
            raise DependencyUnavailableError("llm", "no language model provider is configured")
        self._providers = tuple(providers)
        self._boundaries = boundaries
        self._clients = clients
        self._last_provider: str | None = None

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(provider.name for provider in self._providers)

    @property
    def last_provider(self) -> str | None:
        return self._last_provider

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        wire = [{"role": message.role, "content": message.content} for message in messages]
        errors: list[str] = []

        for index, provider in enumerate(self._providers):
            client = self._clients[provider.name]
            boundary = self._boundaries[provider.name]

            async def operation() -> str:
                return await client.complete(
                    provider.model, wire, temperature, max_tokens
                )

            outcome = await boundary.call(
                operation,
                failure_fallback(f"llm:{provider.name}", "provider_failed"),
                idempotent=True,
            )

            if outcome.ok and outcome.value is not None:
                self._last_provider = provider.name
                return CompletionSchema(
                    text=outcome.value,
                    provider=provider.name,
                    model=provider.model,
                    # Any provider after the first means the primary was down.
                    degraded=index > 0,
                )

            errors.append(f"{provider.name}: {outcome.error}")
            logger.warning("llm provider failed, failing over provider=%s", provider.name)

        raise DependencyUnavailableError("llm", "all providers failed: " + "; ".join(errors))


def build_openai_provider_clients(
    providers: Sequence[LlmProviderConfig],
    client_factory: Callable[..., Any],
) -> dict[str, Any]:
    """Build one AsyncOpenAI-compatible client per provider."""

    class _Client:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            temperature: float,
            max_tokens: int,
        ) -> str:
            response = await self._inner.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""

    return {
        provider.name: _Client(
            client_factory(
                base_url=provider.base_url,
                api_key=provider.api_key or "not-needed",
                timeout=provider.timeout_seconds,
            )
        )
        for provider in providers
    }


def build_provider_boundaries(
    providers: Sequence[LlmProviderConfig],
    boundary_factory: Callable[[str, float], Boundary],
) -> dict[str, Boundary]:
    return {
        provider.name: boundary_factory(f"llm:{provider.name}", provider.timeout_seconds)
        for provider in providers
    }
