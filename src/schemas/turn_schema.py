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
    # Why consolidation reached its verdict, when that is worth naming (for
    # example which active record a duplicate matched, or that a fact was not
    # about the person at all).
    reason: str | None = None
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
    first_turn: bool = False
    # Service-authored lines already appended to ``reply``. Kept as separate
    # fields so a transport can style them without re-parsing the reply, and so
    # the exact text the person saw is testable on its own.
    resume_note: str | None = None
    contradiction_note: str | None = None
    # Set on the first ever turn: the service-authored onboarding that tells the
    # person what this is and what it cannot do.
    onboarding_note: str | None = None
    # Set when the message was a command rather than a conversation turn, so a
    # caller can tell the difference and no turn row is implied.
    command: str | None = None
    # The tools this turn actually ran, in the order they ran. Empty for a turn
    # that used none. This is what makes the agent's work visible on a surface
    # instead of hidden inside the reply text.
    tool_activity: list[str] = Field(default_factory=list)


class CounterfactualSchema(BaseModel):
    turn_id: str
    user_text: str
    with_memory: str
    without_memory: str
    recalled_count: int
    reply_changed: bool
    summary: str
