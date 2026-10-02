"""A deterministic offline model for zero-credential runs, tests and demos.

This is not a language model and it does not pretend to be one. It exists so that
a reviewer can clone the repository, run it with no API keys and no Walrus Memory
account, and still watch extraction, deduplication, supersession and contradiction
detection happen end to end. Production runs use a hosted OpenAI-compatible
provider through :class:`LlmGateway`.

It recognises the three prompt shapes the conversation use case sends by matching
stable substrings of those prompts. ``tests/core/test_offline_llm_gateway.py``
asserts those substrings still appear in the real prompts, so drift breaks the
build instead of silently degrading the demo.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from schemas.llm_schema import (
    ChatMessageSchema,
    CompletionSchema,
    ToolCallSchema,
    ToolDefinitionSchema,
)

EXTRACTION_MARKER = "extract durable facts"
ADJUDICATION_MARKER = "compare one remembered fact"

MEMORY_BLOCK = re.compile(r"<<<[0-9a-f]+>>>\n(.*?)\n<<<END", re.DOTALL)
# Split on sentence ends and on clause joins that introduce another first-person
# statement, so "I am X and I prefer Y" becomes two facts rather than one.
CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?])\s+|\n+|,?\s+and\s+(?=i\b)|\s+but\s+(?=i\b)|,\s+(?=i\b)",
    re.IGNORECASE,
)
TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

STOP_TOKENS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "user",
        "users",
        "about",
        "into",
        "does",
        "did",
        "not",
        "are",
        "was",
        "were",
        "has",
        "have",
        "its",
        "their",
        "they",
        "them",
        "you",
        "your",
        "his",
        "her",
        "she",
        "him",
        "but",
        "all",
        "can",
        "will",
        "just",
        "now",
        "also",
        "very",
        "been",
        "being",
        "when",
        "then",
        "than",
        "there",
        "here",
        "what",
        "which",
        "who",
        "how",
        "why",
    }
)

REWRITES: tuple[tuple[str, str], ...] = (
    # Longer, more specific prefixes must come first: the first match wins.
    ("i always take ", "The user always takes "),
    ("i never ", "The user never "),
    ("i do not ", "The user does not "),
    ("i don't ", "The user does not "),
    ("i am not ", "The user is not "),
    ("i'm not ", "The user is not "),
    ("i am ", "The user is "),
    ("i'm ", "The user is "),
    ("i have ", "The user has "),
    ("i've ", "The user has "),
    ("i like ", "The user likes "),
    ("i love ", "The user loves "),
    ("i prefer ", "The user prefers "),
    ("i work ", "The user works "),
    ("i live ", "The user lives "),
    ("i use ", "The user uses "),
    ("i want ", "The user wants "),
    ("i need ", "The user needs "),
    ("i moved ", "The user moved "),
    ("i keep ", "The user keeps "),
    ("i take ", "The user takes "),
    ("i drink ", "The user drinks "),
    ("i eat ", "The user eats "),
    ("i speak ", "The user speaks "),
    ("i play ", "The user plays "),
    ("i study ", "The user studies "),
    ("i drive ", "The user drives "),
    ("my ", "The user's "),
)

FIRST_PERSON_PREFIXES = ("i ", "i'", "my ", "i'm", "i've", "i'll")

HIGH_STAKES = ("allerg", "epipen", "medicat", "insulin", "asthma", "diagnos", "emergency")
STABLE = ("prefer", "like", "love", "work", "live", "language", "stack", "team", "goal")
NEGATIONS = (" not ", " no ", " never ", " don't ", " doesn't ", " do not ", " does not ")


def _stem(token: str) -> str:
    """Cheap plural strip, so "eats" and "eat" are comparable."""
    if len(token) >= 4 and token.endswith("s"):
        return token[:-1]
    return token


def _content_tokens_in_order(text: str) -> list[str]:
    """Ordered content tokens, so the leading token can act as the predicate."""
    ordered: list[str] = []
    for token in TOKEN_SPLIT.split(text.lower()):
        if len(token) < 3 or token in STOP_TOKENS:
            continue
        stemmed = _stem(token)
        if stemmed not in ordered:
            ordered.append(stemmed)
    return ordered


def _tokens(text: str) -> frozenset[str]:
    """Content tokens. Stopwords are removed before stemming, not after."""
    return frozenset(_content_tokens_in_order(text))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _overlap(left: str, right: str) -> float:
    return _jaccard(_tokens(left), _tokens(right))


def _negated(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(marker in lowered for marker in NEGATIONS)


def _importance(fact: str) -> float:
    lowered = fact.lower()
    if any(cue in lowered for cue in HIGH_STAKES):
        return 1.0
    if any(cue in lowered for cue in STABLE):
        return 0.7
    return 0.4


def _rewrite(sentence: str) -> str:
    lowered = sentence.lower()
    rewritten = sentence
    for prefix, replacement in REWRITES:
        if lowered.startswith(prefix):
            rewritten = replacement + sentence[len(prefix) :]
            break
    else:
        if lowered.startswith("i "):
            rewritten = "The user " + sentence[2:]

    # Residual possessives from the middle of a clause, then one clean full stop.
    rewritten = re.sub(r"\bmy\b", "their", rewritten, flags=re.IGNORECASE)
    rewritten = rewritten.strip().rstrip(".").strip()
    return rewritten + "."


def extract_facts(transcript: str) -> list[dict[str, object]]:
    """Heuristic extraction. Deliberately simple and fully deterministic."""
    user_part = transcript.split("Assistant replied:", 1)[0]
    user_part = user_part.replace("User said:", "", 1)

    facts: list[dict[str, object]] = []
    for raw_sentence in CLAUSE_SPLIT.split(user_part):
        sentence = raw_sentence.strip().strip("-").strip()
        if len(sentence) < 8:
            continue
        lowered = sentence.lower()
        if not lowered.startswith(FIRST_PERSON_PREFIXES):
            continue
        fact = _rewrite(sentence)
        facts.append({"text": fact, "importance": _importance(fact)})
        if len(facts) == 6:
            break
    return facts


def adjudicate(existing: str, candidate: str) -> str:
    """Compare two facts by their leading predicate, then by overlap.

    A bag of words cannot tell "prefers TypeScript" from "prefers Python": they
    share one token and differ in the one that matters. Treating the leading
    content token as the attribute being described fixes that, and lets a value
    swap read as an update rather than an unrelated new fact. A real model does
    this better; this is the honest offline approximation.
    """
    left = _content_tokens_in_order(existing)
    right = _content_tokens_in_order(candidate)
    if not left or not right:
        return "DIFFERENT"

    same_polarity = _negated(existing) == _negated(candidate)
    similarity = _jaccard(frozenset(left), frozenset(right))

    if left[0] == right[0]:
        if left == right:
            return "SAME" if same_polarity else "CONTRADICTS"
        if not same_polarity:
            return "CONTRADICTS"
        # Same attribute, different value. That is a replacement, not a new fact,
        # and not a duplicate however similar the two strings look.
        return "UPDATES"

    if not same_polarity and similarity >= 0.4:
        return "CONTRADICTS"
    if similarity >= 0.75:
        return "SAME"
    return "DIFFERENT"


class OfflineLlm:
    """Drop-in replacement for the configured gateway when no provider is set."""

    def __init__(self) -> None:
        self.name = "offline"

    @property
    def provider_names(self) -> tuple[str, ...]:
        return (self.name,)

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        system = messages[0].content if messages else ""
        user_text = messages[-1].content if messages else ""

        if EXTRACTION_MARKER in system:
            text = json.dumps(extract_facts(user_text))
        elif ADJUDICATION_MARKER in system:
            existing, _, candidate = user_text.partition("CANDIDATE:")
            text = adjudicate(existing.replace("REMEMBERED:", "", 1), candidate)
        else:
            text = self._reply(system, user_text)

        return CompletionSchema(text=text, provider=self.name, model="offline-deterministic")

    async def complete_with_tools(
        self,
        messages: Sequence[ChatMessageSchema],
        tools: Sequence[ToolDefinitionSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        """Deterministic tool calling for zero-credential runs, tests, and demos.

        If a tool result was returned in messages, synthesizes the final reply.
        Otherwise, inspects user intent and dispatches to the corresponding
        tool so that the entire agent loop and tool execution can be verified
        locally without requiring paid API keys.
        """
        tool_messages = [m for m in messages if m.role == "tool"]
        if tool_messages:
            last = tool_messages[-1]
            content = (last.content or "").strip()
            return CompletionSchema(
                text=content or "Tool executed successfully.",
                provider=self.name,
                model="offline-deterministic",
            )

        if not tools:
            return await self.complete(messages, temperature=temperature, max_tokens=max_tokens)

        user_text = ""
        for m in reversed(messages):
            if m.role == "user":
                user_text = (m.content or "").strip()
                break

        if not user_text:
            return await self.complete(messages, temperature=temperature, max_tokens=max_tokens)

        tool_names = {t.name for t in tools}
        call = self._detect_tool_call(user_text, tool_names)
        if call is not None:
            return CompletionSchema(
                text="",
                provider=self.name,
                model="offline-deterministic",
                tool_calls=(call,),
            )

        return await self.complete(messages, temperature=temperature, max_tokens=max_tokens)

    @staticmethod
    def _detect_tool_call(user_text: str, available_tools: set[str]) -> ToolCallSchema | None:
        lowered = user_text.lower().strip()

        # 1. calculate
        if "calculate" in available_tools:
            calc_match = re.search(
                r"^(?:calculate|compute|what\s+is)\s+([0-9\.\s\+\-\*\/\(\)\^\%]+)\??$",
                lowered,
            )
            if calc_match:
                expr = calc_match.group(1).strip()
                return ToolCallSchema(
                    id="call-calc-1", name="calculate", arguments=json.dumps({"expression": expr})
                )
            if re.match(r"^[0-9\.\s\+\-\*\/\(\)\^\%]+$", lowered) and any(
                op in lowered for op in "+-*/%^"
            ):
                return ToolCallSchema(
                    id="call-calc-1",
                    name="calculate",
                    arguments=json.dumps({"expression": user_text.strip()}),
                )

        # 2. weather
        if "weather" in available_tools:
            weather_match = re.search(
                r"(?:weather\s+(?:in|for|at)|what(?:\'s| is)\s+the\s+weather\s+(?:in|for|at))\s+([a-zA-Z\s,]+)\??$",
                lowered,
            )
            if weather_match:
                loc = weather_match.group(1).strip().strip("?. ")
                return ToolCallSchema(
                    id="call-weather-1", name="weather", arguments=json.dumps({"location": loc})
                )

        # 3. wikipedia
        if "wikipedia" in available_tools:
            wiki_match = re.search(
                r"^(?:wikipedia|wiki|look\s+up\s+on\s+wikipedia)\s+(.+)$", lowered
            )
            if wiki_match:
                topic = wiki_match.group(1).strip().strip("?. ")
                return ToolCallSchema(
                    id="call-wiki-1", name="wikipedia", arguments=json.dumps({"query": topic})
                )

        # 4. web_search
        if "web_search" in available_tools:
            search_match = re.search(
                r"^(?:search\s+for|web\s+search|google)\s+(.+)$", lowered
            )
            if search_match:
                q = search_match.group(1).strip().strip("?. ")
                return ToolCallSchema(
                    id="call-search-1", name="web_search", arguments=json.dumps({"query": q})
                )

        # 5. reminder_set
        if "reminder_set" in available_tools:
            remind_match = re.search(
                r"^remind\s+me\s+to\s+(.+?)\s+(?:at|in|on|tomorrow)\s+(.+)$", lowered
            )
            if remind_match:
                text_part = remind_match.group(1).strip()
                when_part = user_text[remind_match.start(2) :].strip().strip("?. ")
                return ToolCallSchema(
                    id="call-remind-1",
                    name="reminder_set",
                    arguments=json.dumps({"text": text_part, "when": when_part}),
                )

        # 6. reminder_list
        if "reminder_list" in available_tools:
            if lowered in (
                "list reminders",
                "show reminders",
                "what are my reminders",
                "what are my reminders?",
                "reminders",
            ):
                return ToolCallSchema(
                    id="call-remind-list-1", name="reminder_list", arguments="{}"
                )

        # 7. memory_recall
        if "memory_recall" in available_tools:
            recall_match = re.search(
                r"^(?:search\s+memory\s+for|recall|what\s+do\s+you\s+remember\s+about)\s+(.+)$",
                lowered,
            )
            if recall_match:
                q = recall_match.group(1).strip().strip("?. ")
                return ToolCallSchema(
                    id="call-recall-1", name="memory_recall", arguments=json.dumps({"query": q})
                )

        # 8. calendar_event
        if "calendar_event" in available_tools:
            cal_match = re.search(
                r"^(?:schedule\s+event|create\s+calendar\s+event|create\s+event)\s+(.+?)\s+(?:on|at)\s+(.+)$",
                lowered,
            )
            if cal_match:
                title_part = cal_match.group(1).strip()
                start_part = user_text[cal_match.start(2) :].strip().strip("?. ")
                return ToolCallSchema(
                    id="call-cal-1",
                    name="calendar_event",
                    arguments=json.dumps({"title": title_part, "start": start_part}),
                )

        # 9. document_question
        if "document_question" in available_tools:
            if (
                "document" in lowered
                or "attached file" in lowered
                or "in this document" in lowered
            ):
                return ToolCallSchema(
                    id="call-doc-1",
                    name="document_question",
                    arguments=json.dumps({"question": user_text.strip()}),
                )

        return None

    def _reply(self, system: str, user_text: str) -> str:
        match = MEMORY_BLOCK.search(system)
        if match is None:
            return (
                "I do not have any memories about you yet, so I can only answer from this "
                f"message. You said: {user_text}"
            )
        remembered = [
            line[2:].strip().rstrip(".")
            for line in match.group(1).splitlines()
            if line.startswith("- ")
        ]
        if not remembered:
            return f"I have nothing stored about you yet. You said: {user_text}"
        joined = "; ".join(remembered[:2])
        return f"From what I remember about you: {joined}. You said: {user_text}"
