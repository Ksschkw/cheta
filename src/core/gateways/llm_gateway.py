"""Language-model gateway with an explicit provider failover chain.

Each provider is its own outbound dependency and therefore gets its own circuit
breaker. A provider that is open is skipped immediately rather than waited on.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from core.config import LlmProviderConfig
from core.errors import DependencyUnavailableError
from core.resilience import (
    Boundary,
    failure_fallback,
    http_status,
    rate_limit_retry_after,
)
from schemas.llm_schema import (
    ChatMessageSchema,
    CompletionSchema,
    RawCompletionSchema,
    ToolCallSchema,
    ToolDefinitionSchema,
)

logger = logging.getLogger("ranti.gateway.llm")

#: Used when a provider returns 429 without a Retry-After header. It is a
#: resting period, not a retirement: the key returns to rotation afterwards.
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0


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


class ProviderRateLimitedError(Exception):
    """Every usable key for one provider is rate limited right now."""

    def __init__(self, provider_name: str, retry_after: float | None) -> None:
        super().__init__(f"all {provider_name} keys are rate limited")
        self.provider_name = provider_name
        # Carried so the boundary recognizes the 429 and does not retry it.
        self.status_code = 429
        self.retry_after = retry_after


class ProviderKeysExhaustedError(Exception):
    """No key for one provider is usable: all were rejected as bad."""

    def __init__(self, provider_name: str) -> None:
        super().__init__(f"no usable {provider_name} key remains")
        self.provider_name = provider_name


def key_label(provider_name: str, index: int, key: str) -> str:
    """A safe way to name a key in a log line, never the key itself."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    prefix = key[:4]
    return f"{provider_name} key #{index + 1} prefix={prefix} sha256={digest}"


class RotatingKeyClient:
    """One provider, many API keys, rotating on a 429 instead of sleeping.

    A provider that rate limits per key can be kept alive with more keys. On a
    429 the next key is tried at once; the limited key rests until the
    Retry-After it gave (or a default when it gave none) and then returns to
    rotation. A key rejected with 401 or 403 is retired for the process. Rotation
    happens on 429 only: any other failure is surfaced immediately so the
    ordinary boundary retry and provider failover stay in charge.

    The whole rotation is one operation from the boundary's point of view, so a
    list of many keys shares the one provider budget rather than multiplying it.
    """

    def __init__(
        self,
        provider_name: str,
        keys: Sequence[str],
        clients: Sequence[ProviderClientProtocol],
        clock: Callable[[], float] | None = None,
    ) -> None:
        if len(keys) != len(clients):
            raise ValueError("every key needs exactly one client")
        self._provider_name = provider_name
        self._keys = tuple(keys)
        self._clients = tuple(clients)
        self._clock = clock or time.monotonic
        self._cursor = 0
        self._unavailable_until: dict[int, float] = {}
        self._retired: set[int] = set()

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def retired_keys(self) -> tuple[int, ...]:
        return tuple(sorted(self._retired))

    async def complete(
        self,
        model: str,
        messages: Sequence[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        return await self._rotate(
            lambda client: client.complete(model, messages, temperature, max_tokens)
        )

    async def complete_with_tools(
        self,
        model: str,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> RawCompletionSchema:
        return await self._rotate(
            lambda client: client.complete_with_tools(
                model, messages, tools, temperature, max_tokens
            )
        )

    async def _rotate(self, attempt):
        now = self._clock()
        count = len(self._clients)
        last_retry_after: float | None = None
        for offset in range(count):
            index = (self._cursor + offset) % count
            if index in self._retired:
                continue
            if self._unavailable_until.get(index, 0.0) > now:
                continue
            try:
                result = await attempt(self._clients[index])
            except Exception as error:  # noqa: BLE001 - classified below
                status = http_status(error)
                if status in (401, 403):
                    self._retired.add(index)
                    logger.warning(
                        "retiring %s for this process: authentication was rejected",
                        key_label(self._provider_name, index, self._keys[index]),
                    )
                    continue
                if status == 429:
                    delay = rate_limit_retry_after(error)
                    if delay is None:
                        delay = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
                    self._unavailable_until[index] = now + delay
                    last_retry_after = (
                        delay if last_retry_after is None else min(last_retry_after, delay)
                    )
                    logger.info(
                        "%s is rate limited for %ss; trying the next key",
                        key_label(self._provider_name, index, self._keys[index]),
                        delay,
                    )
                    continue
                raise
            self._cursor = (index + 1) % count
            return result

        if last_retry_after is not None or count > len(self._retired):
            raise ProviderRateLimitedError(self._provider_name, last_retry_after)
        raise ProviderKeysExhaustedError(self._provider_name)


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

            # A fallback provider cannot always accept another provider's tool
            # calling. Gemini rejects a functionCall part without a
            # thought_signature, which we do not have, so the tool round fails
            # with HTTP 400. Ask the same provider once more with the tools and
            # the tool history removed: a plain, degraded answer is far better
            # than a turn that dies with dependency_unavailable while a provider
            # is reachable.
            if tools and index > 0:
                plain = await self._plain_answer(
                    client, boundary, provider, wire, temperature, max_tokens
                )
                if plain is not None:
                    self._last_provider = provider.name
                    return CompletionSchema(
                        text=plain.text,
                        provider=provider.name,
                        model=provider.model,
                        degraded=True,
                    )
                errors.append(f"{provider.name}: plain fallback produced no answer")

            logger.warning("llm provider failed, failing over provider=%s", provider.name)

        raise DependencyUnavailableError("llm", "all providers failed: " + "; ".join(errors))

    async def _plain_answer(
        self,
        client: ProviderClientProtocol,
        boundary: Boundary,
        provider: LlmProviderConfig,
        wire: Sequence[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> RawCompletionSchema | None:
        """One tool-free attempt at a text answer, for a failover provider."""
        plain_wire = _tool_free_wire(wire)

        async def operation() -> RawCompletionSchema:
            return RawCompletionSchema(
                text=await client.complete(
                    provider.model, plain_wire, temperature, max_tokens
                )
            )

        outcome = await boundary.call(
            operation,
            failure_fallback(f"llm:{provider.name}", "provider_failed"),
            idempotent=True,
        )
        raw = outcome.value if outcome.ok else None
        if raw is not None and raw.text.strip():
            return raw
        return None


def _tool_free_wire(
    wire: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Rewrite a tool conversation as plain messages.

    A provider that did not make the tool call cannot always accept its history:
    Gemini rejects a functionCall part without a thought_signature. The tool
    output is still the useful part of the conversation, so it becomes plain user
    content and the assistant's request becomes a sentence, leaving the fallback
    with a conversation it can answer without tools.
    """
    plain: list[dict[str, object]] = []
    for message in wire:
        role = message.get("role")
        calls = message.get("tool_calls")
        if role == "tool":
            name = str(message.get("name") or "a tool")
            content = str(message.get("content") or "")
            plain.append({"role": "user", "content": f"Result from {name}: {content}"})
        elif role == "assistant" and isinstance(calls, list) and calls:
            names = ", ".join(
                str((call.get("function") or {}).get("name") or "")
                for call in calls
                if isinstance(call, dict)
            )
            text = str(message.get("content") or "").strip()
            note = f"I tried to use these tools: {names}." if names else ""
            plain.append({"role": "assistant", "content": f"{text} {note}".strip()})
        else:
            plain.append(dict(message))
    return plain


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

    built: dict[str, Any] = {}
    for provider in providers:
        # A provider with several keys becomes one client per key behind a
        # rotation. A provider with one key (or none, for Ollama) keeps exactly
        # the single client it had before, so the default deployment is
        # unchanged.
        keys = provider.keys or ("",)
        clients = [
            _Client(
                client_factory(
                    base_url=provider.base_url,
                    api_key=key or "not-needed",
                    timeout=provider.timeout_seconds,
                    # The SDK's own retry cannot be allowed here. On a 429 it
                    # honours Retry-After and sleeps for tens of seconds inside
                    # the call, which the boundary then sees as a timeout.
                    # Retrying is the gateway's job, and a rate limit must rotate
                    # to the next key or the next provider at once instead.
                    max_retries=0,
                )
            )
            for key in keys
        ]
        if len(clients) == 1:
            built[provider.name] = clients[0]
        else:
            built[provider.name] = RotatingKeyClient(provider.name, keys, clients)
    return built


def build_provider_boundaries(
    providers: Sequence[LlmProviderConfig],
    boundary_factory: Callable[[str, float], Boundary],
) -> dict[str, Boundary]:
    return {
        provider.name: boundary_factory(f"llm:{provider.name}", provider.timeout_seconds)
        for provider in providers
    }
