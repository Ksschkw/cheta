"""Transport DTO for one tool execution result.

A tool never raises across the agent loop. It answers with this object, which
carries either content for the model or a readable failure the model can relay
to the person.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResultSchema:
    name: str
    ok: bool
    content: str
    error: str | None = None

    @classmethod
    def success(cls, name: str, content: str) -> ToolResultSchema:
        return cls(name=name, ok=True, content=content)

    @classmethod
    def failure(cls, name: str, error: str) -> ToolResultSchema:
        # The content is what the model reads, so the failure is written as a
        # sentence it can repeat rather than a stack trace it cannot use.
        return cls(name=name, ok=False, content=f"error: {error}", error=error)
