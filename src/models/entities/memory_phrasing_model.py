"""How a stored fact is said back to the person who gave it.

Storage may hold two generations of text. Newer records are written in the
second person ("You prefer jollof rice") because that is what reads correctly
when the same string is shown in a listing, printed in a resume line, or placed
in the model's context. Older records are third person ("The user prefers
jollof rice") and cannot be rewritten on an append-only store, so this module
converts them at render time and leaves anything it does not recognise exactly
as it found it.

It is deliberately free of imports from this project so the rules can be
reasoned about and tested on their own.
"""

from __future__ import annotations

import re

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

# The subject of every rule must be "the user", so a stray "assistant" in the
# object ("Ada works as an assistant") is never mistaken for a fact about the
# bot.
_POSSESSIVE = re.compile(r"^the user's\s+", re.IGNORECASE)

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

_COPULA_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^the user isn't\b", re.IGNORECASE), "You aren't"),
    (re.compile(r"^the user is not\b", re.IGNORECASE), "You are not"),
    (re.compile(r"^the user is\b", re.IGNORECASE), "You are"),
    (re.compile(r"^the user was\b", re.IGNORECASE), "You were"),
    (re.compile(r"^the user wasn't\b", re.IGNORECASE), "You weren't"),
    (re.compile(r"^the user has\b", re.IGNORECASE), "You have"),
    (re.compile(r"^the user hasn't\b", re.IGNORECASE), "You haven't"),
    (re.compile(r"^the user does not\b", re.IGNORECASE), "You do not"),
    (re.compile(r"^the user doesn't\b", re.IGNORECASE), "You don't"),
    (re.compile(r"^the user does\b", re.IGNORECASE), "You do"),
    (re.compile(r"^the user cannot\b", re.IGNORECASE), "You cannot"),
    (re.compile(r"^the user can\b", re.IGNORECASE), "You can"),
    (re.compile(r"^the user will\b", re.IGNORECASE), "You will"),
    (re.compile(r"^the user would\b", re.IGNORECASE), "You would"),
)

_VERB_PHRASE = re.compile(
    r"^the user\s+((?:(?:" + "|".join(_ADVERBS) + r")\s+)*)([A-Za-z]+)\b",
    re.IGNORECASE,
)


def normalise_fact_text(text: str) -> str:
    """Whitespace-collapsed, case-folded text used for deterministic matching."""
    return " ".join(text.split()).strip().casefold()


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


def to_second_person(text: str) -> str:
    """Render a stored fact as something said directly to the person.

    Only the leading "The user ..." subject is rewritten. A record that does not
    match a known pattern is returned unchanged rather than guessed at.
    """
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]

    if _POSSESSIVE.match(stripped):
        return leading + _POSSESSIVE.sub("Your ", stripped, count=1)

    for pattern, replacement in _COPULA_RULES:
        if pattern.match(stripped):
            return leading + pattern.sub(replacement, stripped, count=1)

    match = _VERB_PHRASE.match(stripped)
    if match is not None:
        adverbs = match.group(1)
        verb = match.group(2)
        base = _deconjugate(verb)
        if base != verb.lower():
            rest = stripped[match.end() :]
            return leading + "You " + adverbs + base + rest

    return text


def person_facing(text: str) -> str | None:
    """The text to show the person, or None when it is not about them."""
    if not is_person_fact(text):
        return None
    return to_second_person(text)
