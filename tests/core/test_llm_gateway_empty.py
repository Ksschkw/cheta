"""An empty completion must never be delivered as a reply.

Real users saw "My memory or my model is briefly unavailable" on turns whose
memories were still stored, which means the turn succeeded and only delivery
failed. An empty completion produces an empty Telegram message, and Telegram
answers that with 400 "message text is empty".
"""

from __future__ import annotations

import pytest

from core.config import LlmProviderConfig
from core.errors import DependencyUnavailableError
from core.gateways.llm_gateway import LlmGateway
from core.resilience import Boundary, ResiliencePolicy
from schemas.llm_schema import ChatMessageSchema


class StubClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        self.calls += 1
        return self._text


def gateway_for(texts: dict[str, str]) -> tuple[LlmGateway, dict[str, StubClient]]:
    providers = [
        LlmProviderConfig(name=name, base_url="https://x.invalid", api_key="k", model="m")
        for name in texts
    ]
    clients = {name: StubClient(text) for name, text in texts.items()}
    boundaries = {
        name: Boundary(
            ResiliencePolicy(dependency=f"llm:{name}", timeout_seconds=5.0, max_attempts=1)
        )
        for name in texts
    }
    return LlmGateway(providers, boundaries, clients), clients


async def test_a_whitespace_only_completion_fails_over_to_the_next_provider() -> None:
    gateway, clients = gateway_for({"primary": "   ", "secondary": "A real answer."})

    result = await gateway.complete([ChatMessageSchema(role="user", content="hi")])

    assert result.text == "A real answer."
    assert result.provider == "secondary"
    assert result.degraded is True
    assert clients["primary"].calls == 1


async def test_an_empty_completion_from_every_provider_is_an_explicit_failure() -> None:
    gateway, _ = gateway_for({"primary": "", "secondary": ""})

    with pytest.raises(DependencyUnavailableError) as error:
        await gateway.complete([ChatMessageSchema(role="user", content="hi")])

    assert "empty completion" in str(error.value)


async def test_a_normal_completion_is_untouched() -> None:
    gateway, _ = gateway_for({"primary": "Hello."})

    result = await gateway.complete([ChatMessageSchema(role="user", content="hi")])

    assert result.text == "Hello."
    assert result.degraded is False
