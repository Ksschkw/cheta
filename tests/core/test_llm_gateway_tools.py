"""The gateway must carry OpenAI-compatible tool calls through failover."""

from __future__ import annotations

from collections.abc import Sequence

from core.config import LlmProviderConfig
from core.gateways.llm_gateway import LlmGateway
from core.resilience import Boundary, ResiliencePolicy
from schemas.llm_schema import (
    ChatMessageSchema,
    RawCompletionSchema,
    ToolCallSchema,
    ToolDefinitionSchema,
)


class ToolStubClient:
    def __init__(self, raw: RawCompletionSchema) -> None:
        self.raw = raw
        self.tools_seen: list[list[dict[str, object]]] | None = None
        self.messages_seen: list[dict[str, object]] | None = None

    async def complete(self, model, messages, temperature, max_tokens) -> str:
        return self.raw.text

    async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
        self.tools_seen = list(tools)
        self.messages_seen = list(messages)
        return self.raw


def gateway_for(clients: dict[str, ToolStubClient]) -> LlmGateway:
    providers = [
        LlmProviderConfig(name=name, base_url="https://x.invalid", api_key="k", model="m")
        for name in clients
    ]
    boundaries = {
        name: Boundary(ResiliencePolicy(dependency=f"llm:{name}", timeout_seconds=5.0, max_attempts=1))
        for name in clients
    }
    return LlmGateway(providers, boundaries, clients)


TOOLS = [
    ToolDefinitionSchema(
        name="calculate",
        description="do arithmetic",
        parameters={"type": "object", "properties": {}},
    )
]


async def test_tool_definitions_reach_the_provider_and_calls_come_back() -> None:
    call = ToolCallSchema(id="c1", name="calculate", arguments='{"expression":"1+1"}')
    client = ToolStubClient(RawCompletionSchema(text="", tool_calls=(call,)))
    gateway = gateway_for({"primary": client})

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="2")], TOOLS
    )

    assert client.tools_seen is not None
    assert client.tools_seen[0]["function"]["name"] == "calculate"
    assert result.tool_calls == (call,)
    assert result.tool_calls[0].parsed_arguments() == {"expression": "1+1"}


async def test_an_empty_text_with_tool_calls_is_a_valid_completion() -> None:
    """Reasoning models return no text and only a tool call; that is not empty."""
    call = ToolCallSchema(id="c1", name="calculate", arguments="{}")
    client = ToolStubClient(RawCompletionSchema(text="", tool_calls=(call,)))
    gateway = gateway_for({"primary": client})

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="hi")], TOOLS
    )

    assert result.provider == "primary"


async def test_tool_calls_fail_over_to_the_next_provider() -> None:
    class Broken(ToolStubClient):
        async def complete_with_tools(self, model, messages, tools, temperature, max_tokens):
            raise RuntimeError("down")

    call = ToolCallSchema(id="c2", name="calculate", arguments="{}")
    gateway = gateway_for(
        {
            "primary": Broken(RawCompletionSchema(text="")),
            "secondary": ToolStubClient(RawCompletionSchema(text="ok", tool_calls=(call,))),
        }
    )

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="hi")], TOOLS
    )

    assert result.provider == "secondary"
    assert result.degraded is True
    assert result.tool_calls[0].id == "c2"


async def test_no_tools_means_the_plain_completion_path_is_used() -> None:
    client = ToolStubClient(RawCompletionSchema(text="plain"))
    gateway = gateway_for({"primary": client})

    result = await gateway.complete_with_tools(
        [ChatMessageSchema(role="user", content="hi")], []
    )

    assert result.text == "plain"
    assert client.tools_seen is None


def test_a_message_carries_tool_calls_and_tool_results_on_the_wire() -> None:
    assistant = ChatMessageSchema(
        role="assistant",
        content="",
        tool_calls=(ToolCallSchema(id="c1", name="calculate", arguments='{"a":1}'),),
    )
    tool = ChatMessageSchema(
        role="tool", content="3", tool_call_id="c1", name="calculate"
    )

    wire_assistant = assistant.to_wire()
    wire_tool = tool.to_wire()

    assert wire_assistant["tool_calls"][0]["function"]["name"] == "calculate"
    assert wire_tool["tool_call_id"] == "c1"
    assert wire_tool["role"] == "tool"
