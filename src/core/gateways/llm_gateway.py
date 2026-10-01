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
from schemas.llm_schema import (
    ChatMessageSchema,
    CompletionSchema,
    RawCompletionSchema,
    ToolCallSchema,
    ToolDefinitionSchema,
)

logger = logging.getLogger("ranti.gateway.llm")


class ProviderClientProtocol(Protocol):
    """Minimal AsyncOpenAI surface this gateway needs."""

    async def complete(
        self, model: str, messages: Sequence[dict[str, object]], temperature: float, max_tokens: int
    ) -> str: ...

    async def complete_with_tools(
        self,
        model: str,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> RawCompletionSchema: ...


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
        wire = [message.to_wire() for message in messages]
        errors: list[str] = []
        tools: list[dict[str, object]] = []

        return await self._run(wire, tools, temperature, max_tokens, errors)

    async def complete_with_tools(
        self,
        messages: Sequence[ChatMessageSchema],
        tools: Sequence[ToolDefinitionSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        """Ask the model, offering tools. It may answer with text or with calls."""
        if not tools:
            return await self.complete(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        wire = [message.to_wire() for message in messages]
        payload = [tool.to_openai() for tool in tools]
        return await self._run(wire, payload, temperature, max_tokens, [])

    async def _run(
        self,
        wire: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        temperature: float,
        max_tokens: int,
        errors: list[str],
    ) -> CompletionSchema:
        for index, provider in enumerate(self._providers):
            client = self._clients[provider.name]
            boundary = self._boundaries[provider.name]

            async def operation() -> RawCompletionSchema:
                if tools:
                    return await client.complete_with_tools(
                        provider.model, wire, tools, temperature, max_tokens
                    )
                return RawCompletionSchema(
                    text=await client.complete(provider.model, wire, temperature, max_tokens)
                )

            outcome = await boundary.call(
                operation,
                failure_fallback(f"llm:{provider.name}", "provider_failed"),
                idempotent=True,
            )

            # An empty completion is not an answer. Sending it produces a
            # Telegram 400 ("message text is empty"), which the person sees as
            # "briefly unavailable" even though the model and memory both worked.
            # Reasoning models can return an empty content field with the text in
            # a separate reasoning field, so treat this as a provider failure and
            # fail over rather than delivering nothing. A completion that carries
            # tool calls is not empty even when its text is.
            raw = outcome.value if outcome.ok else None
            if (
                raw is not None
                and (raw.text.strip() or raw.tool_calls)
            ):
                self._last_provider = provider.name
                return CompletionSchema(
                    text=raw.text,
                    provider=provider.name,
                    model=provider.model,
                    # Any provider after the first means the primary was down.
                    degraded=index > 0,
                    tool_calls=raw.tool_calls,
                )

            if outcome.ok:
                errors.append(f"{provider.name}: empty completion")
                logger.warning("llm provider returned an empty completion: %s", provider.name)
            else:
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
            messages: Sequence[dict[str, object]],
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

        async def complete_with_tools(
            self,
            model: str,
            messages: Sequence[dict[str, object]],
            tools: Sequence[dict[str, object]],
            temperature: float,
            max_tokens: int,
        ) -> RawCompletionSchema:
            response = await self._inner.chat.completions.create(
                model=model,
                messages=list(messages),
                tools=list(tools),
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            message = response.choices[0].message
            calls: list[ToolCallSchema] = []
            for raw in message.tool_calls or []:
                function = raw.function
                calls.append(
                    ToolCallSchema(
                        id=str(raw.id),
                        name=str(function.name),
                        arguments=str(function.arguments or "{}"),
                    )
                )
            return RawCompletionSchema(
                text=message.content or "", tool_calls=tuple(calls)
            )

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
