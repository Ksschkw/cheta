"""Turn and recall transport DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TurnRequestSchema(BaseModel):
    surface: str = Field(min_length=1, max_length=32)
    surface_user_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)
    memory_enabled: bool = True


class RecalledMemoryView(BaseModel):
    blob_id: str
    text: str
    distance: float
    salience: float
    importance: float
    origin_surface: str
    age_days: float


class ExtractedFactView(BaseModel):
    text: str
    verdict: str
    blob_id: str | None = None
    # True when the relayer has only accepted the write as a job. The blob id is
    # then a local placeholder and the memory is not yet persisted, so the
    # transport must not present it as stored.
    pending: bool = False


class TurnSchema(BaseModel):
    turn_id: str
    user_id: str
    memory_namespace: str
    reply: str
    recalled: list[RecalledMemoryView]
    stored_facts: list[ExtractedFactView]
    skipped_duplicates: int
    contradiction_count: int
    memory_degraded: bool
    memory_note: str | None = None
    provider: str | None = None


class CounterfactualSchema(BaseModel):
    turn_id: str
    user_text: str
    with_memory: str
    without_memory: str
    recalled_count: int
    reply_changed: bool
    summary: str
