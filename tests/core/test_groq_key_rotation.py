"""Groq key rotation: a rate-limited key costs milliseconds, not a minute.

An owner can hold several Groq keys. On a 429 the provider must try the next key
at once, rest the limited key until its Retry-After, retire a key that is simply
bad, and still fall through to the next provider when every key is limited.
"""

from __future__ import annotations

import logging
import time

from core.config import LlmProviderConfig, Settings
from core.gateways.llm_gateway import (
    RotatingKeyClient,
    build_openai_provider_clients,
)
from core.resilience import Boundary, ResiliencePolicy
from schemas.llm_schema import (
    ChatMessageSchema,
    RawCompletionSchema,
    ToolDefinitionSchema,
)

TOOLS = [
    ToolDefinitionSchema(name="reminder_set", description="set a reminder", parameters={})
]


class FakeStatusError(Exception):
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.retry_after = retry_after


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeKeyClient:
    """One key. ``behavior`` is ``ok:<text>``, ``429:<retry_after>`` or ``401``."""

    def __init__(self, key: str, behavior: str) -> None:
        self.key = key
        self.behavior = behavior
        self.calls = 0

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        self.calls += 1
        return await self._result()

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        self.calls += 1
        return RawCompletionSchema(text=await self._result())

    async def _result(self) -> str:
        if self.behavior.startswith("429"):
            retry = self.behavior.partition(":")[2]
            raise FakeStatusError(429, retry or None)
        if self.behavior == "401":
            raise FakeStatusError(401)
        return self.behavior.partition(":")[2] or "ok"


class TextStubClient:
    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        return self.text

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        return RawCompletionSchema(text=self.text)


def groq_settings(**env: str) -> LlmProviderConfig:
    settings = Settings.from_env(env)
    return next(provider for provider in settings.llm_providers if provider.name == "groq")


def test_groq_api_keys_parses_trims_dedupes_and_preserves_order() -> None:
    provider = groq_settings(GROQ_API_KEYS="  key1 , key2 ,key1, ,  key3  ,")

    assert provider.keys == ("key1", "key2", "key3")


def test_only_groq_api_key_still_behaves_as_a_single_key() -> None:
    provider = groq_settings(GROQ_API_KEY="single-key")

    assert provider.keys == ("single-key",)

    built = build_openai_provider_clients([provider], lambda **kwargs: object())

    assert not isinstance(built["groq"], RotatingKeyClient)


def test_the_key_list_wins_when_both_forms_are_set() -> None:
    provider = groq_settings(GROQ_API_KEY="single-key", GROQ_API_KEYS="k1,k2")

    assert provider.keys == ("k1", "k2")


def test_a_key_list_builds_one_rotating_client() -> None:
    provider = groq_settings(GROQ_API_KEYS="k1,k2,k3")

    built = build_openai_provider_clients([provider], lambda **kwargs: object())

    client = built["groq"]
    assert isinstance(client, RotatingKeyClient)
    assert client.key_count == 3


async def test_a_429_rotates_to_the_next_key_without_sleeping() -> None:
    first = FakeKeyClient("k1", "429:60")
    second = FakeKeyClient("k2", "ok:second key answered")
    client = RotatingKeyClient("groq", ("k1", "k2"), [first, second])

    started = time.monotonic()
    text = await client.complete("m", [], 0.2, 100)
    elapsed = time.monotonic() - started

    assert text == "second key answered"
    assert first.calls == 1
    assert second.calls == 1
    assert elapsed < 0.1, f"a 429 rotation took {elapsed:.3f}s"


async def test_a_limited_key_rests_and_a_good_key_is_preferred() -> None:
    clock = FakeClock()
    limited = FakeKeyClient("k1", "429:60")
    good = FakeKeyClient("k2", "ok:good")
    client = RotatingKeyClient(
        "groq", ("k1", "k2"), [limited, good], clock=clock
    )

    await client.complete("m", [], 0.2, 100)
    assert limited.calls == 1
    assert good.calls == 1

    # The limited key is still inside its Retry-After, so it is not touched.
    await client.complete("m", [], 0.2, 100)
    assert limited.calls == 1
    assert good.calls == 2


async def test_a_key_returns_to_rotation_after_its_retry_after() -> None:
    clock = FakeClock()
    limited = FakeKeyClient("k1", "429:60")
    good = FakeKeyClient("k2", "ok:good")
    client = RotatingKeyClient(
        "groq", ("k1", "k2"), [limited, good], clock=clock
    )

    await client.complete("m", [], 0.2, 100)
    clock.advance(61)
    limited.behavior = "ok:recovered"

    text = await client.complete("m", [], 0.2, 100)

    assert text == "recovered"
    assert limited.calls == 2


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def test_a_rejected_key_is_retired_and_never_logged() -> None:
    bad = FakeKeyClient("badkey-secret-123", "401")
    good = FakeKeyClient("goodkey-secret-456", "ok:fine")
    client = RotatingKeyClient("groq", (bad.key, good.key), [bad, good])

    # Capture on the gateway's own logger. The architecture test runs
    # import-linter, whose dictConfig disables existing loggers, so caplog would
    # be order-dependent here.
    handler = CapturingHandler()
    target = logging.getLogger("ranti.gateway.llm")
    was_disabled = target.disabled
    target.disabled = False
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        text = await client.complete("m", [], 0.2, 100)
        await client.complete("m", [], 0.2, 100)
    finally:
        target.removeHandler(handler)
        target.disabled = was_disabled

    assert text == "fine"
    assert bad.calls == 1, "a retired key must not be tried again"
    assert client.retired_keys == (0,)

    for message in handler.messages:
        assert bad.key not in message
        assert good.key not in message
    assert any("key #1" in message for message in handler.messages)


async def test_every_key_limited_fails_over_to_the_next_provider() -> None:
    first = FakeKeyClient("k1", "429:60")
    second = FakeKeyClient("k2", "429:60")
    rotating = RotatingKeyClient("groq", ("k1", "k2"), [first, second])

    providers = [
        LlmProviderConfig(
            name="groq", base_url="https://groq.invalid", api_key="", model="m",
            api_keys=("k1", "k2"),
        ),
        LlmProviderConfig(
            name="gemini", base_url="https://gemini.invalid", api_key="g", model="m"
        ),
    ]
    boundaries = {
        "groq": Boundary(
            ResiliencePolicy(dependency="llm:groq", timeout_seconds=10.0, max_attempts=2)
        ),
        "gemini": Boundary(
            ResiliencePolicy(dependency="llm:gemini", timeout_seconds=5.0, max_attempts=1)
        ),
    }
    clients = {"groq": rotating, "gemini": TextStubClient("gemini answered")}
    from core.gateways.llm_gateway import LlmGateway

    gateway = LlmGateway(providers, boundaries, clients)  # type: ignore[arg-type]

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="remind me")], TOOLS
    )

    assert result.provider == "gemini"
    assert result.text == "gemini answered"
    assert result.degraded is True
    assert first.calls == 1
    assert second.calls == 1
