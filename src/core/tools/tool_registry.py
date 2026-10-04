"""A plugin registry for agent tools.

A tool is one declaration: a name, a description, a JSON schema for its
arguments, and a callable. Adding a tool is adding a declaration here or in one
of the sibling modules and registering it in the composition root; the
conversation service never changes.

Two rules make a tool safe to hand to a model:

1. Execution is bounded. Every call runs under its own timeout and every
   failure, including a timeout, comes back as a readable result instead of an
   exception, so a broken tool can never crash a turn.
2. The registry is the capability list. The model is told exactly the definitions
   that exist here and nothing else, so it can never claim a capability the
   application does not have.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from schemas.llm_schema import ToolDefinitionSchema
from schemas.tool_schema import ToolResultSchema

logger = logging.getLogger("ranti.tools")

# Default ceiling for one tool call. A network tool also runs its own outbound
# boundary; this is the outer bound that guarantees the agent loop always moves.
DEFAULT_TOOL_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ToolContext:
    """Who the tool is acting for. Pure data, no dependencies."""

    user_id: str
    namespace: str
    surface: str
    # The identity this surface uses for the person, used when a reminder has to
    # fall back to it because no push recipient was supplied.
    surface_user_id: str = ""
    # The address a pushed message can be sent to on this surface, when the
    # surface has one. Telegram has a chat id; a web request does not.
    recipient_id: str = ""
    document_text: str | None = None
    # The name of the document read for this turn, so the system prompt can name
    # it in plain words instead of the model having to guess or deny it.
    document_name: str = ""
    # True only when this surface can hand the person a file. A tool whose real
    # output is a document uses this to say what it could or could not deliver
    # instead of pretending, and the service uses it to decide whether to send.
    can_send_documents: bool = False


ToolHandler = Callable[[dict[str, object], ToolContext], Awaitable[ToolResultSchema]]


@dataclass(frozen=True)
class ToolSpec:
    """One declared capability."""

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    # True when the handler reaches a third-party service. Those handlers go
    # through an outbound Boundary inside the gateway; a local pure tool such as
    # calculate does not, because there is no network failure domain to isolate.
    uses_network: bool = False


class ToolRegistry:
    """Holds the tools and runs them with a bound and a typed result."""

    def __init__(self, tools: Iterable[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for spec in tools:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if not spec.name:
            raise ValueError("a tool must have a name")
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._tools[spec.name] = spec

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinitionSchema]:
        """The capability list handed to the model, in registration order."""
        return [
            ToolDefinitionSchema(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
            )
            for spec in self._tools.values()
        ]

    def describe(self) -> str:
        """A plain-text version for the system prompt, one line per tool."""
        lines = [f"- {spec.name}: {spec.description}" for spec in self._tools.values()]
        return "\n".join(lines)

    async def execute(
        self, name: str, arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        """Run one tool. This method never raises."""
        spec = self._tools.get(name)
        if spec is None:
            logger.warning("model asked for unknown tool %s", name)
            return ToolResultSchema.failure(
                name,
                f"there is no tool named {name}; the available tools are "
                + ", ".join(self._tools),
            )
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                return await spec.handler(arguments, context)
        except asyncio.TimeoutError:
            logger.warning("tool timed out name=%s", name)
            return ToolResultSchema.failure(
                name, f"the {name} tool timed out after {spec.timeout_seconds:g} seconds"
            )
        except asyncio.CancelledError:
            # The caller cancelled the turn; that is not a tool failure and must
            # propagate so the event loop stays honest.
            raise
        except Exception as error:  # noqa: BLE001 - a tool must never crash a turn
            logger.warning(
                "tool failed name=%s error=%s", name, type(error).__name__, exc_info=True
            )
            return ToolResultSchema.failure(
                name, f"the {name} tool failed ({type(error).__name__})"
            )


def require_string(arguments: dict[str, object], key: str) -> str:
    """Read a required string argument, or explain the miss as a real error.

    A model sometimes omits a field or sends the wrong type. That is a failed
    call, not a crash, so the caller turns this into a tool failure result.
    """
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"argument {key!r} must be a non-empty string")
    return value.strip()


def optional_int(
    arguments: dict[str, object], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = arguments.get(key, default)
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


class ToolRegistryProtocol(Protocol):
    """What the conversation service needs from the registry."""

    def names(self) -> tuple[str, ...]: ...

    def get(self, name: str) -> ToolSpec | None: ...

    def definitions(self) -> list[ToolDefinitionSchema]: ...

    def describe(self) -> str: ...

    async def execute(
        self, name: str, arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema: ...


@dataclass
class ToolCallRecord:
    """Kept for observability: one executed call and whether it succeeded."""

    name: str
    ok: bool
    detail: str = ""
    tags: dict[str, str] = field(default_factory=dict)
