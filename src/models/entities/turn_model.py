"""Turn entity: one exchange on one surface. Imports nothing from this project."""

from __future__ import annotations

from dataclasses import dataclass

VALID_ROLES = ("user", "assistant")


@dataclass(frozen=True)
class TurnModel:
    """A recorded exchange, including what memory was used and what it changed."""

    id: str
    user_id: str
    surface: str
    user_text: str
    assistant_text: str
    recalled_blob_ids: tuple[str, ...]
    memory_enabled: bool
    counterfactual_text: str | None
    created_at: str

    def __post_init__(self) -> None:
        if not self.id or not self.user_id:
            raise ValueError("turn id and user_id are required")
        if not self.user_text.strip():
            raise ValueError("turn user_text must not be blank")
        if self.memory_enabled and not self.assistant_text.strip():
            raise ValueError("a memory-backed turn must have an assistant reply")
