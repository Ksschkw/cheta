"""Provider failover: a 429 must be fast, and a failover must still answer.

Production logs showed the OpenAI SDK sleeping on 429 for up to 53 seconds and a
tool-call failover to Gemini dying with HTTP 400 every time. These tests pin the
two fixes: the SDK never retries internally, and a provider that cannot accept a
tool-call history is asked for a plain answer instead of being given up on.
"""

from __future__ import annotations

import contextlib
import http.server
import threading
import time
from collections.abc import Iterator

from core.config import LlmProviderConfig
from core.gateways.llm_gateway import (
    LlmGateway,
    build_openai_provider_clients,
)
from core.resilience import Boundary, ResiliencePolicy
from schemas.llm_schema import (
    ChatMessageSchema,
    CompletionSchema,
    RawCompletionSchema,
    ToolDefinitionSchema,
)

from openai import AsyncOpenAI

TOOLS = [
    ToolDefinitionSchema(
        name="reminder_set",
        description="set a reminder",
        parameters={"type": "object", "properties": {}},
    )
]


class FakeStatusError(Exception):
    """Stands in for an OpenAI or memwal HTTP status error."""

    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.retry_after = retry_after


class TextStubClient:
    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        return self.text

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        return RawCompletionSchema(text=self.text)


class RateLimitedClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        return ""

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        self.calls += 1
        raise FakeStatusError(429, retry_after="60")


class BrokenClient:
    async def complete(self, model, messages, temperature, max_tokens) -> str:
        raise RuntimeError("provider down")

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        raise RuntimeError("provider down")


class ToolIncapableClient:
    """Reads plain messages but rejects any conversation carrying tool calls."""

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        return "a plain answer without tools"

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        raise FakeStatusError(400)


def gateway_for(clients: dict[str, object]) -> LlmGateway:
    providers = [
        LlmProviderConfig(
            name=name,
            base_url="https://provider.invalid",
            api_key="key",
            model="model",
            timeout_seconds=10.0,
        )
        for name in clients
    ]
    boundaries = {
        name: Boundary(
            ResiliencePolicy(
                dependency=f"llm:{name}", timeout_seconds=10.0, max_attempts=2
            )
        )
        for name in clients
    }
    return LlmGateway(providers, boundaries, clients)  # type: ignore[arg-type]


@contextlib.contextmanager
def rate_limited_provider(retry_after: str) -> Iterator[tuple[str, dict[str, int]]]:
    """A local server that answers every completion with a 429."""
    counter = {"requests": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            counter["requests"] += 1
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            payload = b'{"error":{"message":"rate limited","type":"rate_limit_error"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", retry_after)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", counter
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_the_sdk_internal_retry_is_disabled() -> None:
    """The SDK must not sleep on a 429; retrying is the gateway's job."""
    captured: dict[str, object] = {}

    def factory(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    providers = [
        LlmProviderConfig(name="groq", base_url="https://x.invalid", api_key="k", model="m")
    ]

    build_openai_provider_clients(providers, factory)

    assert captured["max_retries"] == 0


def test_a_real_provider_client_is_built_with_no_internal_retries() -> None:
    providers = [
        LlmProviderConfig(name="groq", base_url="https://x.invalid", api_key="k", model="m")
    ]

    clients = build_openai_provider_clients(providers, AsyncOpenAI)

    assert clients["groq"]._inner.max_retries == 0


async def test_a_provider_429_fails_over_without_a_long_sdk_sleep() -> None:
    """A 429 with Retry-After must not keep the turn waiting for the SDK."""
    with rate_limited_provider(retry_after="2") as (base_url, counter):
        primary = LlmProviderConfig(
            name="groq", base_url=base_url, api_key="k", model="m", timeout_seconds=10.0
        )
        secondary = LlmProviderConfig(
            name="gemini", base_url="https://x.invalid", api_key="k", model="m"
        )
        clients = build_openai_provider_clients([primary], AsyncOpenAI)
        clients["gemini"] = TextStubClient("fallback answer")
        boundaries = {
            "groq": Boundary(
                ResiliencePolicy(dependency="llm:groq", timeout_seconds=10.0, max_attempts=2)
            ),
            "gemini": Boundary(
                ResiliencePolicy(dependency="llm:gemini", timeout_seconds=5.0, max_attempts=1)
            ),
        }
        gateway = LlmGateway([primary, secondary], boundaries, clients)  # type: ignore[arg-type]

        started = time.monotonic()
        result = await gateway.complete_with_tools(
            [ChatMessageSchema(role="user", content="remind me")], TOOLS
        )
        elapsed = time.monotonic() - started

    assert result.provider == "gemini"
    assert result.text == "fallback answer"
    assert elapsed < 1.0, f"the turn spent {elapsed:.2f}s before failing over"
    assert counter["requests"] == 1, "the SDK retried the rate-limited provider"


async def test_a_rate_limited_first_provider_still_produces_a_reply() -> None:
    primary = RateLimitedClient()
    gateway = gateway_for({"groq": primary, "gemini": TextStubClient("fallback answer")})

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="remind me")], TOOLS
    )

    assert result.provider == "gemini"
    assert result.text == "fallback answer"
    assert result.degraded is True
    # The 429 was not retried, so the fallback got the turn immediately.
    assert primary.calls == 1


async def test_a_fallback_that_cannot_do_tool_calls_still_answers() -> None:
    gateway = gateway_for({"groq": BrokenClient(), "gemini": ToolIncapableClient()})

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="remind me")], TOOLS
    )

    assert result.provider == "gemini"
    assert result.text == "a plain answer without tools"
    assert result.tool_calls == ()


def test_a_completion_schema_still_carries_a_provider_name() -> None:
    """Guard the shape failover returns; a wrong field would break callers."""
    completion = CompletionSchema(text="hi", provider="gemini", model="m", degraded=True)

    assert completion.provider == "gemini"
    assert completion.degraded is True
