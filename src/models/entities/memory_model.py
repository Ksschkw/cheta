"""Memory entity. Imports nothing from this project."""

from __future__ import annotations

from dataclasses import dataclass

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_CONTRADICTED = "contradicted"
VALID_STATUSES = (STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_CONTRADICTED)


@dataclass(frozen=True)
class MemoryModel:
    """One fact Walrus Memory is holding for a user.

    ``blob_id`` is the identifier the relayer returned when the memory was
    persisted. It is the only handle a caller gets back from recall, which is
    why every consolidation decision is keyed on it.
    """

    id: str
    user_id: str
    blob_id: str
    namespace: str
    text: str
    importance: float
    origin_surface: str
    status: str
    superseded_by: str | None
    occurred_at: str
    created_at: str

    def __post_init__(self) -> None:
        if not self.id or not self.blob_id:
            raise ValueError("memory id and blob_id are required")
        if not self.text.strip():
            raise ValueError("memory text must not be blank")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {self.status!r}")
        if self.status == STATUS_SUPERSEDED and not self.superseded_by:
            raise ValueError("a superseded memory must name the memory that replaced it")
        if not self.namespace:
            raise ValueError("namespace is required")

    @property
    def is_live(self) -> bool:
        return self.status == STATUS_ACTIVE
