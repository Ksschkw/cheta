"""Device pairing: issue a code, redeem it, see and manage the shared clients.

Everything runs against the real SQLite index and the offline Walrus Memory
mock; only the model is faked, because the model is not what is under test.
"""

from __future__ import annotations

import asyncio
import re

from models.entities.pairing_code_model import (
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    pairing_code_hash,
)
from tests.services.test_conversation_service import Harness

CODE_PATTERN = re.compile("[" + PAIRING_CODE_ALPHABET + "]{6,10}")


def first_code(reply: str) -> str:
    match = CODE_PATTERN.search(reply)
    assert match is not None, f"no pairing code in reply: {reply!r}"
    return match.group(0)


class RecordingChannel:
    """A reply channel that records what the originator was told."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_message(
        self, recipient_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.sent.append((recipient_id, text))

    async def send_document(
        self,
        recipient_id: str,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        return None

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None:
        return None

    async def send_typing(self, recipient_id: str) -> None:
        return None

    async def send_photo(
        self,
        recipient_id: str,
        image_path: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        return None


async def issue(harness: Harness, surface: str, user: str, name: str) -> str:
    reply = await harness.service.answer_command("/pair", surface, user, name)
    return first_code(reply)


async def pair(
    harness: Harness,
    code: str,
    surface: str,
    user: str,
    name: str,
) -> str:
    return await harness.service.answer_command("pair", surface, user, name, code)


# 1. /pair issues a code and states the expiry.


async def test_pair_issues_a_code_and_states_the_expiry() -> None:
    harness = Harness([])

    reply = await harness.service.answer_command("/pair", "telegram", "42", "Ada")

    code = first_code(reply)
    assert len(code) == PAIRING_CODE_LENGTH
    assert "Enter this on the other client" in reply
    assert "expires in 10 minutes" in reply
    rows = harness.database.fetch_all("SELECT code_hash FROM pairing_codes")
    assert len(rows) == 1
    assert rows[0]["code_hash"] != code
    assert rows[0]["code_hash"] == pairing_code_hash(
        harness.settings.pairing_hash_pepper, code
    )


# 2. pair <code> binds the new client to the same namespace as the originator.


async def test_pairing_binds_the_new_client_to_the_same_namespace() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say_settled("I am allergic to peanuts")
    code = await issue(harness, "telegram", "42", "Ada")

    redeemed = await pair(harness, code, "cli", "kossi", "Ada")

    originator = harness.users.get_by_identity("telegram", "42")
    newcomer = harness.users.get_by_identity("cli", "kossi")
    assert originator is not None and newcomer is not None
    assert originator.memory_key == newcomer.memory_key
    assert "paired with Ada's memory space" in redeemed

    result = await harness.service.handle_turn("cli", "kossi", "Ada", "peanuts")
    assert any("peanuts" in memory.text for memory in result.recalled), (
        "a fact written on the originator must be recalled on the paired client"
    )


# 3. An expired code is refused, and the message says it expired.


async def test_an_expired_code_is_refused_and_says_it_expired() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    harness.database.execute(
        "UPDATE pairing_codes SET expires_at = ?", ("2000-01-01T00:00:00+00:00",)
    )

    refused = await pair(harness, code, "cli", "kossi", "Ada")

    assert "expired" in refused.lower()
    newcomer = harness.users.get_by_identity("cli", "kossi")
    assert newcomer is not None and newcomer.memory_handle is None


# 4. A redeemed code cannot be redeemed twice, and the second attempt says used.


async def test_a_redeemed_code_cannot_be_redeemed_twice() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    first = await pair(harness, code, "cli", "kossi", "Ada")
    assert "paired with" in first

    second = await pair(harness, code, "web", "widget", "Ada")

    assert "already used" in second.lower()
    newcomer = harness.users.get_by_identity("web", "widget")
    assert newcomer is not None and newcomer.memory_handle is None


# 5. A wrong code is refused and says the code is unknown.


async def test_a_wrong_code_is_refused_and_says_it_is_unknown() -> None:
    harness = Harness([])
    await issue(harness, "telegram", "42", "Ada")

    refused = await pair(harness, "ZZZZZZ", "cli", "kossi", "Ada")

    assert "unknown" in refused.lower()


# 6. Two concurrent redemptions of the same code: exactly one succeeds.


async def test_two_concurrent_redemptions_leave_exactly_one_winner() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")

    replies = await asyncio.gather(
        pair(harness, code, "cli", "kossi", "Ada"),
        pair(harness, code, "web", "widget", "Ada"),
    )

    winners = [reply for reply in replies if "paired with" in reply]
    losers = [reply for reply in replies if "already used" in reply.lower()]
    assert len(winners) == 1
    assert len(losers) == 1
    bound = [
        user
        for user in (
            harness.users.get_by_identity("cli", "kossi"),
            harness.users.get_by_identity("web", "widget"),
        )
        if user is not None and user.memory_handle is not None
    ]
    assert len(bound) == 1


# 7. The originator is notified, and the notification names the linking surface.


async def test_the_originator_is_told_which_surface_linked() -> None:
    channel = RecordingChannel()
    harness = Harness([], reply_channel=channel)
    code = await issue(harness, "telegram", "42", "Ada")

    await pair(harness, code, "cli", "kossi", "Kossi")

    assert channel.sent, "the originator must be told that a client linked"
    recipient, text = channel.sent[-1]
    assert recipient == "42"
    assert "cli" in text
    assert "Kossi" in text


async def test_a_failed_notification_does_not_fail_the_redemption() -> None:
    channel = RecordingChannel(fail=True)
    harness = Harness([], reply_channel=channel)
    code = await issue(harness, "telegram", "42", "Ada")

    redeemed = await pair(harness, code, "cli", "kossi", "Ada")

    assert "paired with" in redeemed
    newcomer = harness.users.get_by_identity("cli", "kossi")
    assert newcomer is not None and newcomer.memory_handle is not None


# 8. /sessions lists both clients and marks the current one.


async def test_sessions_lists_both_clients_and_marks_the_current_one() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    await pair(harness, code, "cli", "kossi", "Kossi")

    listing = await harness.service.answer_command("/sessions", "cli", "kossi", "Kossi")

    lines = listing.splitlines()
    cli_lines = [line for line in lines if re.match(r"^\d+\. cli ", line)]
    telegram_lines = [line for line in lines if re.match(r"^\d+\. telegram ", line)]
    assert len(cli_lines) == 1 and "Kossi" in cli_lines[0]
    assert len(telegram_lines) == 1 and "Ada" in telegram_lines[0]
    assert "[this client]" in cli_lines[0]
    assert "[this client]" not in telegram_lines[0]
    assert "linked" in listing


# 9. /unpair returns the caller to its per-surface namespace; the other keeps it.


async def test_unpair_returns_the_caller_to_its_own_namespace() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    await pair(harness, code, "cli", "kossi", "Kossi")

    reply = await harness.service.answer_command("/unpair", "cli", "kossi", "Kossi")

    newcomer = harness.users.get_by_identity("cli", "kossi")
    originator = harness.users.get_by_identity("telegram", "42")
    assert newcomer is not None and originator is not None
    assert newcomer.memory_handle is None
    assert newcomer.memory_key == "cli-kossi"
    assert originator.memory_handle is not None
    assert "own memory space" in reply


async def test_unpair_by_number_removes_the_listed_client() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    await pair(harness, code, "cli", "kossi", "Kossi")
    listing = await harness.service.answer_command("/sessions", "telegram", "42", "Ada")
    cli_index = 0
    for line in listing.splitlines():
        match = re.match(r"^(\d+)\. cli ", line)
        if match is not None:
            cli_index = int(match.group(1))
            break
    assert cli_index > 0

    reply = await harness.service.answer_command(
        "/unpair", "telegram", "42", "Ada", str(cli_index)
    )

    newcomer = harness.users.get_by_identity("cli", "kossi")
    originator = harness.users.get_by_identity("telegram", "42")
    assert newcomer is not None and newcomer.memory_handle is None
    assert originator is not None and originator.memory_handle is not None
    assert "cli" in reply


# 10. A client outside the shared space cannot unpair a member of it.


async def test_a_client_outside_the_space_cannot_unpair_a_member() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")
    await pair(harness, code, "cli", "kossi", "Kossi")
    harness.users.get_or_create("web", "widget", "Guest")

    refused = await harness.service.answer_command("/unpair", "web", "widget", "Guest", "1")

    assert "not in a shared space" in refused
    originator = harness.users.get_by_identity("telegram", "42")
    newcomer = harness.users.get_by_identity("cli", "kossi")
    assert originator is not None and originator.memory_handle is not None
    assert newcomer is not None and newcomer.memory_handle is not None


# An ordinary sentence that starts with "pair" stays a conversation.


async def test_a_plain_sentence_starting_with_pair_is_not_a_redemption() -> None:
    harness = Harness([])

    result = await harness.service.handle_turn(
        "telegram", "42", "Ada", "pair of shoes are more comfortable"
    )

    assert result.command is None
    assert result.provider != "command"
    assert "unknown" not in result.reply.lower()


# The chosen replacement policy, stated as a test.
async def test_a_second_pair_replaces_the_previous_live_code() -> None:
    harness = Harness([])
    first = await harness.service.answer_command("/pair", "telegram", "42", "Ada")
    first_code_value = first_code(first)
    second = await harness.service.answer_command("/pair", "telegram", "42", "Ada")
    second_code_value = first_code(second)
    assert first_code_value != second_code_value
    assert "stopped working" in second

    refused = await pair(harness, first_code_value, "cli", "kossi", "Ada")
    assert "replaced" in refused.lower()
    accepted = await pair(harness, second_code_value, "web", "widget", "Ada")
    assert "paired with" in accepted
