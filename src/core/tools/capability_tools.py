"""Capability tools: things the application can already do, named for the model.

These do not perform new work. They make an existing capability visible in the
tool list, so the model knows it exists instead of denying it. The tool list is
the capability list, and a capability that is real but unlisted is one the model
will wrongly say it does not have.
"""

from __future__ import annotations

from core.attachment_parser import MAX_EXTRACTED_CHARS
from core.tools.tool_registry import ToolContext, ToolSpec
from schemas.tool_schema import ToolResultSchema

READABLE = "PDF, plain text, markdown, CSV and DOCX"


def build_capability_tools() -> list[ToolSpec]:
    async def document_question(
        arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        if context.document_text and context.document_text.strip():
            text = context.document_text[:MAX_EXTRACTED_CHARS]
            return ToolResultSchema.success(
                "document_question",
                "The document for this turn is already in the conversation. Its "
                f"extracted text follows so you can answer from it:\n{text}",
            )
        return ToolResultSchema.success(
            "document_question",
            "There is no document in this conversation yet. Ask the person to "
            f"send one of these formats, up to 20 MB: {READABLE}.",
        )

    return [
        ToolSpec(
            name="document_question",
            description=(
                "Answer a question about a document the person uploaded. When a "
                f"document ({READABLE}, up to 20 MB) is attached, its extracted "
                "text is already placed in the conversation, so answer directly "
                "from it; call this tool to retrieve that text again or to check "
                "whether a document is present. Scanned images inside a PDF are "
                "images and cannot be read."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What the person is asking about the document.",
                    }
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            handler=document_question,
            timeout_seconds=5.0,
        )
    ]
