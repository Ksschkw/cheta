"""Memory tools: the assistant acting on its own store."""

from __future__ import annotations

from core.tools.memory_access import (
    DEFAULT_RECALL_LIMIT,
    MAX_RECALL_LIMIT,
    MemoryAccessProtocol,
)
from core.tools.tool_registry import (
    ToolContext,
    ToolSpec,
    optional_int,
    require_string,
)
from schemas.tool_schema import ToolResultSchema


def build_memory_tools(access: MemoryAccessProtocol) -> list[ToolSpec]:
    async def recall(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        query = require_string(arguments, "query")
        limit = optional_int(arguments, "limit", DEFAULT_RECALL_LIMIT, 1, MAX_RECALL_LIMIT)
        lines = await access.recall(context.user_id, context.namespace, query, limit)
        if not lines:
            return ToolResultSchema.success("memory_recall", "nothing stored matches that query")
        return ToolResultSchema.success(
            "memory_recall", "Stored notes that match:\n" + "\n".join(lines)
        )

    async def remember(
        arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        text = require_string(arguments, "text")
        outcome = await access.remember(
            context.user_id, context.namespace, context.surface, text
        )
        return ToolResultSchema.success("memory_remember", outcome)

    async def forget(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        raw = arguments.get("index")
        try:
            index = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ToolResultSchema.failure("memory_forget", "index must be a whole number")
        outcome = access.forget(context.user_id, index)
        return ToolResultSchema.success("memory_forget", outcome)

    return [
        ToolSpec(
            name="memory_recall",
            description=(
                "Search what you have stored about this person and return the "
                "matching notes, each with the number used by memory_forget. Use "
                "this when the remembered facts already in the conversation are "
                "not enough. Never claim to remember something this did not return."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "limit": {
                        "type": "integer",
                        "description": f"How many notes, 1 to {MAX_RECALL_LIMIT}.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=recall,
            timeout_seconds=8.0,
        ),
        ToolSpec(
            name="memory_remember",
            description=(
                "Store one durable fact about this person. The text is stored "
                "exactly as given, so write it as a self-contained sentence in "
                "the third person. Only use this when the person asked you to "
                "remember something; ordinary conversation is already mined for "
                "durable facts automatically."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One self-contained fact about the person.",
                    }
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=remember,
            timeout_seconds=12.0,
        ),
        ToolSpec(
            name="memory_forget",
            description=(
                "Retire one stored note so it stops being recalled. The index is "
                "the number shown by memory_recall or by the /memories listing. "
                "Retiring does not erase the blob, because Walrus Memory cannot "
                "erase; say that plainly if asked."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "The number of the note to retire.",
                    }
                },
                "required": ["index"],
                "additionalProperties": False,
            },
            handler=forget,
            timeout_seconds=8.0,
        ),
    ]
