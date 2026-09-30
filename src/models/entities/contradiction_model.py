"""Contradiction entity: two memories that cannot both be true."""

from __future__ import annotations

from dataclasses import dataclass

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
VALID_STATUSES = (STATUS_OPEN, STATUS_RESOLVED)


@dataclass(frozen=True)
class ContradictionModel:
    """Surfaced for a human decision, because append-only storage cannot decide."""

    id: str
    user_id: str
    left_blob_id: str
    right_blob_id: str
    reason: str
    status: str
    created_at: str
    resolved_at: str | None

    def __post_init__(self) -> None:
        if not self.id or not self.user_id:
            raise ValueError("contradiction id and user_id are required")
        if self.left_blob_id == self.right_blob_id:
            raise ValueError("a contradiction needs two different memories")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        if not self.reason.strip():
            raise ValueError("a contradiction must explain itself")
        if self.status == STATUS_RESOLVED and not self.resolved_at:
            raise ValueError("a resolved contradiction needs a resolution timestamp")
