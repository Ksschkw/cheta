"""Reminder tools: set and list. Delivery is the scheduler's job, not the model's."""

from __future__ import annotations

from datetime import UTC, datetime

from core.tools.calendar_tools import build_ics_event
from core.tools.reminder_store import ReminderStoreProtocol
from core.tools.tool_registry import ToolContext, ToolSpec, require_string
from models.entities.reminder_model import (
    MAX_REMINDER_TEXT_CHARS,
    WHEN_FORMATS,
    format_due,
    resolve_due_at,
)
from schemas.tool_schema import ToolDocumentSchema, ToolResultSchema

LIST_LIMIT = 20
# A reminder is a moment, but a calendar entry needs an end. Thirty minutes is
# a short, harmless block the person can drag; the reminder itself still fires
# at the exact due time.
REMINDER_EVENT_MINUTES = 30
REMINDER_FILENAME = "cheta-reminder.ics"


def _reminder_document(text: str, due: datetime, now: datetime) -> ToolDocumentSchema:
    """Build the reminder as an .ics document through the shared generator.

    It calls build_ics_event rather than formatting a second calendar file, so
    the escape rules, the value types and the UID handling can never drift
    between an event and a reminder.
    """
    content = build_ics_event(
        title=text,
        start=due,
        duration_minutes=REMINDER_EVENT_MINUTES,
        description="A reminder set through the assistant.",
        now=now,
    )
    return ToolDocumentSchema(
        filename=REMINDER_FILENAME,
        content=content.encode("utf-8"),
        media_type="text/calendar",
        caption=(
            "Open this file to add the reminder to your calendar. "
            "I cannot add it to your calendar myself."
        ),
    )


def build_reminder_tools(store: ReminderStoreProtocol) -> list[ToolSpec]:
    async def set_reminder(
        arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        text = require_string(arguments, "text")
        when = require_string(arguments, "when")
        if len(text) > MAX_REMINDER_TEXT_CHARS:
            return ToolResultSchema.failure(
                "reminder_set", f"the reminder text is longer than {MAX_REMINDER_TEXT_CHARS} characters"
            )
        now = datetime.now(UTC)
        try:
            due = resolve_due_at(when, now)
        except ValueError as error:
            return ToolResultSchema.failure("reminder_set", str(error))
        recipient = context.recipient_id or context.surface_user_id
        reminder = store.create(
            user_id=context.user_id,
            surface=context.surface,
            recipient_id=recipient,
            text=text,
            due_at=due.isoformat(timespec="seconds"),
        )
        if not recipient:
            return ToolResultSchema.success(
                "reminder_set",
                f"Saved: I will remind you at {format_due(due)}. This surface "
                "cannot receive a pushed message, so the reminder will only be "
                "delivered on a surface that can, such as Telegram.",
            )
        if context.can_send_documents:
            return ToolResultSchema.success(
                "reminder_set",
                f"Saved: I will remind you at {format_due(due)} with: {text} "
                f"(reminder id {reminder.id[:8]}). I am also sending it as a "
                "calendar file you can open.",
                document=_reminder_document(text, due, now),
            )
        return ToolResultSchema.success(
            "reminder_set",
            f"Saved: I will remind you at {format_due(due)} with: {text} "
            f"(reminder id {reminder.id[:8]}).",
        )

    async def list_reminders(
        arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        pending = store.list_pending(context.user_id, LIST_LIMIT)
        if not pending:
            return ToolResultSchema.success(
                "reminder_list", "there are no pending reminders for this person"
            )
        lines = ["Pending reminders:"]
        for index, reminder in enumerate(pending, start=1):
            lines.append(f"{index}. {format_due(datetime.fromisoformat(reminder.due_at))}: {reminder.text}")
        return ToolResultSchema.success("reminder_list", "\n".join(lines))

    return [
        ToolSpec(
            name="reminder_set",
            description=(
                "Set a reminder that the application will deliver later. Use it "
                "whenever someone asks to be reminded. 'when' must be "
                f"{WHEN_FORMATS}; do not guess at other formats. Delivery is a "
                "real pushed message on the surface the reminder was set from. "
                "On a surface that can receive files it also sends the reminder "
                "as an .ics calendar entry, built by the same generator as "
                "calendar_event; it still cannot add anything to a calendar by "
                "itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What to remind the person about.",
                    },
                    "when": {
                        "type": "string",
                        "description": "When to deliver it, for example +30m or 2026-01-31T09:00:00Z.",
                    },
                },
                "required": ["text", "when"],
                "additionalProperties": False,
            },
            handler=set_reminder,
            timeout_seconds=8.0,
        ),
        ToolSpec(
            name="reminder_list",
            description=(
                "List this person's pending reminders with their due times. Use "
                "it when someone asks what is scheduled."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_reminders,
            timeout_seconds=8.0,
        ),
    ]
