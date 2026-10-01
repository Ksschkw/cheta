"""How a stored fact is said back to the person who gave it.

Storage may hold two generations of text. Newer records are written in the
second person ("You prefer jollof rice") because that is what reads correctly
when the same string is shown in a listing, printed in a resume line, or placed
in the model's context. Older records are third person ("The user prefers
jollof rice", "Kosisochukwu is a software engineering student") and cannot be
rewritten on an append-only store, so this module converts them at render time
and leaves anything it does not recognise exactly as it found it.

The extractor was told to write about the person and often used their actual
name as the subject, so the display name is accepted alongside "The user". A
sentence that merely mentions the name is never rewritten: every rule is
anchored to the leading subject, so "You told Kosisochukwu about it" is left
alone.

It is deliberately free of imports from this project so the rules can be
reasoned about and tested on their own.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# A fact about the assistant or the product is not a fact about the person, even
# when the sentence starts with "The user". Both the extraction rule and the
# display layer use these predicates.
_ASSISTANT_SUBJECT = re.compile(
    r"\b(?:the|your|this|that)\s+(?:assistant|bot|chatbot)\b"
    r"|\bassistant's\b"
    r"|\b(?:assistant|chatbot)\s+(?:is|was|has|can|should|must|will|would|identifies)\b",
    re.IGNORECASE,
)

_CONVERSATION_SUBJECT = re.compile(
    r"\b(?:the|this|that)\s+(?:conversation|chat|turn|message|reply|transcript)\b"
    r"|\bthe user\s+(?:said|asked|told|mentioned|replied|wrote|was told)\b",
    re.IGNORECASE,
)

_IRREGULAR_VERBS = {
    "goes": "go",
    "says": "say",
    "has": "have",
    "does": "do",
}

# Adverbs that can sit between the subject and its verb. Without this list
# "The user currently owns ..." would be read as a verb phrase beginning at
# "currently".
_ADVERBS = (
    "currently",
    "now",
    "also",
    "still",
    "often",
    "usually",
    "sometimes",
    "always",
    "never",
    "really",
    "generally",
    "typically",
    "mainly",
    "mostly",
    "already",
    "just",
    "only",
    "particularly",
    "especially",
    "frequently",
    "rarely",
    "recently",
    "previously",
)

# The subject the conversion recognises. Both the generic "The user" and the
# person's own display name are leading subjects; the pattern is anchored, so a
# name that merely appears later in the sentence is untouched.
_SUBJECT_TEMPLATES: tuple[tuple[str, str], ...] = (
    (r"{subject} isn't\b", "You aren't"),
    (r"{subject} is not\b", "You are not"),
    (r"{subject} is\b", "You are"),
    (r"{subject} was\b", "You were"),
    (r"{subject} wasn't\b", "You weren't"),
    (r"{subject} has\b", "You have"),
    (r"{subject} hasn't\b", "You haven't"),
    (r"{subject} does not\b", "You do not"),
    (r"{subject} doesn't\b", "You don't"),
    (r"{subject} does\b", "You do"),
    (r"{subject} cannot\b", "You cannot"),
    (r"{subject} can\b", "You can"),
    (r"{subject} will\b", "You will"),
    (r"{subject} would\b", "You would"),
)

_POSSESSIVE_TEMPLATE = r"{subject}'s\s+"
_VERB_PHRASE_TEMPLATE = (
    r"{subject}\s+((?:(?:" + "|".join(_ADVERBS) + r")\s+)*)([A-Za-z]+)\b"
)


def normalise_fact_text(text: str) -> str:
    """Whitespace-collapsed, case-folded text used for deterministic matching."""
    return " ".join(text.split()).strip().casefold()


def subject_names(display_name: str | None) -> tuple[str, ...]:
    """The name forms that can be the leading subject of a stored fact.

    The full display name and its first word are both accepted, because an
    extractor that met "Ada Lovelace" may later write "Ada is ...". The list is
    ordered longest first so a full name is tried before its first word.
    """
    if not display_name:
        return ()
    cleaned = " ".join(display_name.split())
    if not cleaned:
        return ()
    names = [cleaned]
    first = cleaned.split(" ", 1)[0]
    if len(first) >= 3 and first != cleaned:
        names.append(first)
    names.sort(key=len, reverse=True)
    return tuple(names)


def _subject_pattern(display_names: Sequence[str]) -> str:
    """A leading-subject alternation: "The user" plus every accepted name form."""
    alternatives = [r"the\s+user"]
    for name in display_names:
        cleaned = " ".join(str(name).split())
        if not cleaned:
            continue
        escaped = re.escape(cleaned)
        if escaped not in alternatives:
            alternatives.append(escaped)
    alternatives.sort(key=len, reverse=True)
    return "(?:" + "|".join(alternatives) + ")"


def is_assistant_fact(text: str) -> bool:
    """True when the sentence is about the assistant rather than the person."""
    return _ASSISTANT_SUBJECT.search(text) is not None


def is_conversation_fact(text: str) -> bool:
    """True when the sentence is about the conversation rather than the person."""
    return _CONVERSATION_SUBJECT.search(text) is not None


def is_person_fact(text: str) -> bool:
    """True when the sentence can honestly be listed as a fact about the person."""
    return not is_assistant_fact(text) and not is_conversation_fact(text)


def _deconjugate(verb: str) -> str:
    """Turn a third-person-singular verb into the second-person base form."""
    low = verb.lower()
    if low in _IRREGULAR_VERBS:
        return _IRREGULAR_VERBS[low]
    if low.endswith("ies") and len(low) > 3:
        return low[:-3] + "y"
    if low.endswith(("ches", "shes", "sses", "xes", "zes")):
        return low[:-2]
    if low.endswith("es") and len(low) > 2:
        return low[:-1]
    if low.endswith("s") and not low.endswith("ss"):
        return low[:-1]
    return low


def to_second_person(text: str, display_names: Sequence[str] = ()) -> str:
    """Render a stored fact as something said directly to the person.

    Only the leading subject is rewritten, whether that subject is "The user"
    or the person's own display name. A record that does not match a known
    pattern is returned unchanged rather than guessed at.
    """
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    subject = _subject_pattern(display_names)

    possessive = re.compile("^" + _POSSESSIVE_TEMPLATE.format(subject=subject), re.IGNORECASE)
    if possessive.match(stripped):
        return leading + possessive.sub("Your ", stripped, count=1)

    for template, replacement in _SUBJECT_TEMPLATES:
        pattern = re.compile("^" + template.format(subject=subject), re.IGNORECASE)
        if pattern.match(stripped):
            return leading + pattern.sub(replacement, stripped, count=1)

    verb_phrase = re.compile("^" + _VERB_PHRASE_TEMPLATE.format(subject=subject), re.IGNORECASE)
    match = verb_phrase.match(stripped)
    if match is not None:
        adverbs = match.group(1)
        verb = match.group(2)
        base = _deconjugate(verb)
        if base != verb.lower():
            rest = stripped[match.end() :]
            return leading + "You " + adverbs + base + rest

    return text


def person_facing(text: str, display_names: Sequence[str] = ()) -> str | None:
    """The text to show the person, or None when it is not about them."""
    if not is_person_fact(text):
        return None
    return to_second_person(text, display_names)


def record_facing(record, display_names: Sequence[str] = ()) -> str:
    """The single rendering every surface uses to turn a record into display text.

    A record that is not about the person is still returned verbatim rather than
    raising, so a caller that has already filtered can never crash; a caller that
    has not is responsible for filtering it out first.
    """
    return person_facing(record.text, display_names) or record.text
