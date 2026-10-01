"""Reminder entity and the pure 'when' parser. Imports nothing from this project.

A reminder is one thing a person asked to be told at a time. The parser is
deliberately narrow: relative offsets and ISO 8601, with no natural-language
guessing, so the model cannot promise a time the application cannot compute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
VALID_STATUSES = (STATUS_PENDING, STATUS_SENDING, STATUS_SENT)

MAX_REMINDER_TEXT_CHARS = 500
# A reminder further ahead than this is almost always a mis-parsed date.
MAX_HORIZON_DAYS = 366

_RELATIVE = re.compile(
    r"^(?:in\s+)?\+?(\d+)\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
    r"h|hr|hrs|hour|hours|d|day|days)$"
)
_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

WHEN_FORMATS = (
    "a relative offset such as +30m, +2h or +1d, or an ISO 8601 timestamp such "
    "as 2026-01-31T09:00:00Z"
)


@dataclass(frozen=True)
class ReminderModel:
    id: str
    user_id: str
    surface: str
    recipient_id: str
    text: str
    due_at: str
    status: str
    created_at: str
    sent_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.user_id:
            raise ValueError("reminder id and user_id are required")
        if not self.text.strip():
            raise ValueError("reminder text must not be blank")
        if len(self.text) > MAX_REMINDER_TEXT_CHARS:
            raise ValueError("reminder text is too long")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {self.status!r}")
        if not self.due_at:
            raise ValueError("a reminder must have a due time")


def resolve_due_at(when: str, now: datetime) -> datetime:
    """Turn a when string into an aware UTC datetime, or raise ValueError."""
    text = when.strip()
    if not text:
        raise ValueError(f"when must be {WHEN_FORMATS}")
    match = _RELATIVE.match(text.lower())
    if match is not None:
        amount = int(match.group(1))
        seconds = amount * _UNIT_SECONDS[match.group(2)]
        due = now + timedelta(seconds=seconds)
    else:
        candidate = text
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as error:
            raise ValueError(f"when must be {WHEN_FORMATS}") from error
        due = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    due = due.astimezone(UTC)
    if due > now + timedelta(days=MAX_HORIZON_DAYS):
        raise ValueError(f"that is more than {MAX_HORIZON_DAYS} days away")
    return due


def format_due(due: datetime) -> str:
    return due.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
