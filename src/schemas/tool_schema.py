"""Transport DTO for one tool execution result.

A tool never raises across the agent loop. It answers with this object, which
carries either content for the model or a readable failure the model can relay
to the person.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDocumentSchema:
    """A file a tool produced, for the surface to deliver as an attachment.

    A tool handler is pure: it shapes bytes but never touches a transport. When
    a capability's real output is a file (an .ics calendar entry, for example),
    the handler returns it here and the conversation service, which owns the
    reply channel, decides whether it can actually hand it to the person.
    """

    filename: str
    content: bytes
    media_type: str = "application/octet-stream"
    caption: str | None = None


@dataclass(frozen=True)
class ToolResultSchema:
    name: str
    ok: bool
    content: str
    error: str | None = None
    document: ToolDocumentSchema | None = None

    @classmethod
    def success(
        cls,
        name: str,
        content: str,
        document: ToolDocumentSchema | None = None,
    ) -> ToolResultSchema:
        return cls(name=name, ok=True, content=content, document=document)

    @classmethod
    def failure(cls, name: str, error: str) -> ToolResultSchema:
        # The content is what the model reads, so the failure is written as a
        # sentence it can repeat rather than a stack trace it cannot use.
        return cls(name=name, ok=False, content=f"error: {error}", error=error)
