"""Transport DTOs for the language-model boundary.

The same shapes carry plain text and OpenAI-compatible tool calling. A message
may ask for a tool and a completion may answer with tool calls instead of text;
the agent loop in the conversation service is the only thing that has to know
the difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCallSchema:
    """One tool the model asked the application to run."""

    id: str
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict[str, object]:
        """Best-effort argument decode. A malformed payload is an empty call.

        The model's JSON is untrusted input. A tool that receives no arguments
        will fail its own schema check and report that back; it is never handed
        a half-parsed object.
        """
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def to_wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass(frozen=True)
class ToolDefinitionSchema:
    """One callable capability advertised to the model."""

    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=dict)

    def to_openai(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ChatMessageSchema:
    """One chat message sent to or returned by a model."""

    role: str
    content: str
    # Set on an assistant message that requested tools.
    tool_calls: tuple[ToolCallSchema, ...] = ()
    # Set on a tool message answering one call.
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, object]:
        message: dict[str, object] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.to_wire() for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            message["name"] = self.name
        return message


@dataclass(frozen=True)
class RawCompletionSchema:
    """What one provider client returned, before failover and validation."""

    text: str
    tool_calls: tuple[ToolCallSchema, ...] = ()


@dataclass(frozen=True)
class CompletionSchema:
    """A model completion plus the provider that actually served it."""

    text: str
    provider: str
    model: str
    degraded: bool = False
    tool_calls: tuple[ToolCallSchema, ...] = ()
