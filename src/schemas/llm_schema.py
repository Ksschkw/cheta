"""Transport DTOs for the language-model boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessageSchema:
    """One chat message sent to or returned by a model."""

    role: str
    content: str


@dataclass(frozen=True)
class CompletionSchema:
    """A model completion plus the provider that actually served it."""

    text: str
    provider: str
    model: str
    degraded: bool = False
