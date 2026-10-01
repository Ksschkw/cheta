"""Calendar tools: turn one event into an iCalendar (.ics) document.

WHY A FILE AND NOT A CALENDAR API. Writing directly into someone's Google,
Outlook or Apple calendar needs an OAuth application, per-person consent,
token storage with refresh, and for Google a verification review. None of that
can be built or hosted for free on this deployment, and keeping other people's
calendar tokens safe is not something this project should promise. An .ics file
is the honest zero-cost path: it is a plain-text RFC 5545 document that every
calendar application on every platform already understands. The person opens
the file and their own calendar asks whether to add the event. No account, no
API key, no OAuth consent, no app review, no network.

NO CIRCUIT BREAKER. Every function here is pure text generation in this
process. Nothing opens a socket and nothing depends on a remote service, so
there is no remote failure domain to isolate. A breaker would add latency and
hide bugs. The tool registry still applies its own timeout, which is what
bounds a pathological input.

HONESTY. This feature can put a file in someone's hand. It cannot know whether
their calendar accepted it, so no message from here ever says an event was
added to a calendar.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta

from core.tools.tool_registry import ToolContext, ToolSpec, require_string
from schemas.tool_schema import ToolDocumentSchema, ToolResultSchema

ICS_PRODID = "-//Cheta//Calendar//EN"
DEFAULT_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 60 * 24 * 30
MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 1000
DEFAULT_FILENAME = "cheta-event.ics"

# The format the tool asks for when it cannot parse the time. A parse failure
# must name the accepted forms rather than invent a time.
START_FORMATS = (
    "an ISO 8601 date-time such as 2026-01-31T09:00:00Z or "
    "2026-01-31T09:00:00+01:00, or a simple phrase such as 'tomorrow 09:00' "
    "or 'next monday at 10:00'"
)
START_FORMATS_ALL_DAY = (
    "an ISO date such as 2026-01-31, or a simple phrase such as 'tomorrow' or "
    "'next monday'"
)

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|z|[+-]\d{2}:?\d{2})?$"
)
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?\s*(am|pm)?$")
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_WEEKDAYS = {name: index for index, name in enumerate(_WEEKDAY_NAMES)}
_RELATIVE_DAYS = {"today": 0, "tonight": 0, "tomorrow": 1}

_TRUE_WORDS = {"true", "1", "yes", "y", "on"}


# ------------------------------------------------------------------ parsing


def _parse_clock(text: str) -> time | None:
    """Parse a clock such as 09:30, 9:30pm or 9am. Deterministic, no guessing."""
    match = _CLOCK.match(text.strip().lower())
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    second = int(match.group(3) or 0)
    meridiem = match.group(4)
    if meridiem is not None:
        if hour < 1 or hour > 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


def _parse_iso_datetime(text: str) -> datetime | None:
    """Parse an ISO 8601 date-time, defaulting a missing offset to UTC.

    A naive timestamp is UTC, never local time. The classic calendar bug is
    treating local time as UTC and shifting the event by the offset; this code
    never labels local time as UTC because it never assumes local time.
    """
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    else:
        # Python accepts +0130, but normalise it so 3.11 is never ambiguous.
        match = re.search(r"([+-]\d{2})(\d{2})$", candidate)
        if match is not None:
            candidate = f"{candidate[: match.start()]}{match.group(1)}:{match.group(2)}"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _split_natural_day(text: str, now: datetime) -> tuple[date, str] | None:
    """Split a phrase into a calendar date and whatever time text follows it.

    Returns None when the phrase names no day. The time text may be empty, which
    the caller treats differently for an all-day event and a timed one.
    """
    low = " ".join(text.strip().lower().split())
    if not low:
        return None

    first = low.split(" ", 1)[0].strip(",")
    if first in _RELATIVE_DAYS:
        rest = low[len(first) :].strip(" ,")
        return (now + timedelta(days=_RELATIVE_DAYS[first])).date(), rest

    match = re.match(
        r"^(?:(next|this)\s+)?(" + "|".join(_WEEKDAY_NAMES) + r")\b\s*(.*)$", low
    )
    if match is None:
        return None
    qualifier = match.group(1)
    target = _WEEKDAYS[match.group(2)]
    rest = match.group(3).strip(" ,")
    delta = (target - now.weekday()) % 7
    if qualifier == "next" and delta == 0:
        # "next monday" on a Monday means the one a week away, not today.
        delta = 7
    return (now + timedelta(days=delta)).date(), rest


def parse_event_start(
    text: str, *, all_day: bool, now: datetime
) -> date | datetime:
    """Turn a start string into a date for an all-day event, else an aware datetime.

    Raises ValueError with a readable explanation when the string does not name
    a time in a form this function can compute without guessing.
    """
    raw = text.strip()
    if not raw:
        raise ValueError(f"start must be {START_FORMATS}")
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    if all_day:
        if _DATE_ONLY.match(raw):
            return date.fromisoformat(raw)
        split = _split_natural_day(raw, now)
        if split is not None:
            # A time on an all-day request is ignored rather than guessed at.
            return split[0]
        raise ValueError(f"for an all-day event, start must be {START_FORMATS_ALL_DAY}")

    if _DATE_ONLY.match(raw):
        raise ValueError(
            f"{raw!r} is a date with no time. Give a time, for example "
            f"{raw}T09:00:00Z, or set all_day=true for an all-day event."
        )

    if _ISO_DATETIME.match(raw):
        parsed = _parse_iso_datetime(raw)
        if parsed is not None:
            return parsed
        raise ValueError(f"start must be {START_FORMATS}")

    split = _split_natural_day(raw, now)
    if split is not None:
        day, rest = split
        rest = rest.lstrip()
        if rest.startswith("at "):
            rest = rest[3:].strip()
        if rest.startswith("@"):
            rest = rest[1:].strip()
        clock = _parse_clock(rest) if rest else None
        if clock is None:
            raise ValueError(
                f"start must be {START_FORMATS}; {raw!r} names a day but no time"
            )
        return datetime.combine(day, clock, tzinfo=UTC)

    raise ValueError(f"start must be {START_FORMATS}")


# ------------------------------------------------------------------- ICS


def _escape_text(value: str) -> str:
    """Escape one iCalendar text value per RFC 5545 section 3.3.11.

    Backslash first, or the escapes added afterwards would be escaped again.
    A raw newline inside SUMMARY or DESCRIPTION makes the whole file malformed
    and a calendar app rejects the lot, not just the one line.
    """
    text = value.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n")
    return text


def _fold(line: str, limit: int = 75) -> list[str]:
    """Fold a content line at 75 octets, continuation lines starting with a space.

    Folding is byte-aware so a multi-byte character is never split in half. A
    line that is already short stays exactly as it is.
    """
    if len(line.encode("utf-8")) <= limit:
        return [line]
    physical: list[str] = []
    current = ""
    current_bytes = 0
    for character in line:
        width = len(character.encode("utf-8"))
        if current and current_bytes + width > limit:
            physical.append(current)
            current = " " + character
            current_bytes = 1 + width
        else:
            current += character
            current_bytes += width
    physical.append(current)
    return physical


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics_event(
    *,
    title: str,
    start: date | datetime,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    description: str = "",
    location: str = "",
    all_day: bool = False,
    now: datetime | None = None,
    uid: str | None = None,
) -> str:
    """Build a complete one-event VCALENDAR document as text.

    This is the single generation path. The calendar tool and the reminder tool
    both call it, so an event and a reminder can never drift into two formats.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    event_uid = uid or f"{uuid.uuid4().hex}@cheta"

    if all_day:
        if isinstance(start, datetime):
            start_date = start.date()
        elif isinstance(start, date):
            start_date = start
        else:
            raise ValueError("an all-day event needs a date")
        # DTEND for a DATE value is exclusive, so a one-day event ends the next
        # day. Using the same day would make the event invisible in some apps.
        start_line = f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}"
        end_line = f"DTEND;VALUE=DATE:{(start_date + timedelta(days=1)).strftime('%Y%m%d')}"
    else:
        if not isinstance(start, datetime):
            raise ValueError("a timed event needs a date and a time")
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        start_utc = start.astimezone(UTC)
        end_utc = start_utc + timedelta(minutes=duration_minutes)
        start_line = f"DTSTART:{_utc_stamp(start_utc)}"
        end_line = f"DTEND:{_utc_stamp(end_utc)}"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{_escape_text(event_uid)}",
        f"DTSTAMP:{_utc_stamp(moment)}",
        start_line,
        end_line,
        f"SUMMARY:{_escape_text(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape_text(description)}")
    if location:
        lines.append(f"LOCATION:{_escape_text(location)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])

    physical: list[str] = []
    for line in lines:
        physical.extend(_fold(line))
    # RFC 5545 requires CRLF line endings; a bare LF is a common reason a file
    # is rejected as malformed.
    return "\r\n".join(physical) + "\r\n"


# ------------------------------------------------------------------ tool


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    return False


def _read_duration(value: object) -> int:
    if value is None:
        return DEFAULT_DURATION_MINUTES
    if isinstance(value, bool):
        raise ValueError("duration_minutes must be a whole number of minutes")
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("duration_minutes must be a whole number of minutes") from error
    if minutes < 1:
        raise ValueError("duration_minutes must be at least 1")
    if minutes > MAX_DURATION_MINUTES:
        raise ValueError(
            f"duration_minutes must be no more than {MAX_DURATION_MINUTES} "
            "(thirty days)"
        )
    return minutes


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _describe_start(start: date | datetime, all_day: bool) -> str:
    if all_day:
        day = start.date() if isinstance(start, datetime) else start
        return f"{day.isoformat()} (all day)"
    assert isinstance(start, datetime)
    return start.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def _calendar_event(
    arguments: dict[str, object], context: ToolContext
) -> ToolResultSchema:
    try:
        title = require_string(arguments, "title")
        start_raw = require_string(arguments, "start")
    except ValueError as error:
        return ToolResultSchema.failure("calendar_event", str(error))
    if len(title) > MAX_TITLE_CHARS:
        return ToolResultSchema.failure(
            "calendar_event", f"the title is longer than {MAX_TITLE_CHARS} characters"
        )
    description = _optional_text(arguments.get("description"))
    location = _optional_text(arguments.get("location"))
    if len(description) > MAX_TEXT_CHARS or len(location) > MAX_TEXT_CHARS:
        return ToolResultSchema.failure(
            "calendar_event",
            f"the description and location must each be at most {MAX_TEXT_CHARS} characters",
        )
    all_day = _as_bool(arguments.get("all_day"))
    try:
        duration = _read_duration(arguments.get("duration_minutes"))
        start = parse_event_start(start_raw, all_day=all_day, now=datetime.now(UTC))
        content = build_ics_event(
            title=title,
            start=start,
            duration_minutes=duration,
            description=description,
            location=location,
            all_day=all_day,
        )
    except ValueError as error:
        return ToolResultSchema.failure("calendar_event", str(error))

    when = _describe_start(start, all_day)
    if not context.can_send_documents:
        return ToolResultSchema.success(
            "calendar_event",
            f"I prepared a calendar file for '{title}' starting {when}, but this "
            "surface cannot send files, so I could not give it to you here. "
            "Nothing was added to your calendar. Ask me on Telegram and I will "
            "send you the .ics file to open.",
        )

    caption = (
        f"Open this file to add '{title}' to your calendar. "
        "I cannot add it to your calendar myself."
    )
    document = ToolDocumentSchema(
        filename=DEFAULT_FILENAME,
        content=content.encode("utf-8"),
        media_type="text/calendar",
        caption=caption,
    )
    return ToolResultSchema.success(
        "calendar_event",
        f"I prepared a calendar file for '{title}' starting {when} and I am "
        "sending it to you as an .ics document now. Open it to add the event to "
        "your calendar. I cannot add it to your calendar myself.",
        document=document,
    )


def build_calendar_tools() -> list[ToolSpec]:
    """Build the calendar tool. Local and pure, so it carries no network flag."""
    return [
        ToolSpec(
            name="calendar_event",
            description=(
                "Create one calendar event and send it to the person as an .ics "
                "file. This is the zero-cost way to put something on a calendar: "
                ".ics is the plain iCalendar standard every calendar application "
                "already understands, so it needs no account, no API key, no "
                "OAuth consent and no network. The person opens the file and "
                "their own calendar adds the event. You cannot add anything to "
                "their calendar yourself and must never say that you did. "
                f"'start' accepts {START_FORMATS}; without an offset the time is "
                "UTC, never a guessed local time. Set all_day to true and give a "
                "date for an all-day event. 'duration_minutes' defaults to "
                f"{DEFAULT_DURATION_MINUTES}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "What the event is called.",
                    },
                    "start": {
                        "type": "string",
                        "description": (
                            "When it starts. An ISO date-time with or without an "
                            "explicit offset, or a simple phrase. Without an "
                            "offset the time is UTC."
                        ),
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": (
                            f"How long it lasts in minutes, 1 to "
                            f"{MAX_DURATION_MINUTES}. Defaults to "
                            f"{DEFAULT_DURATION_MINUTES}."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional details for the event.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional place for the event.",
                    },
                    "all_day": {
                        "type": "boolean",
                        "description": (
                            "True for a whole-day event. 'start' must then be a "
                            "date, not a date-time. Defaults to false."
                        ),
                    },
                },
                "required": ["title", "start"],
                "additionalProperties": False,
            },
            handler=_calendar_event,
            timeout_seconds=5.0,
        )
    ]
