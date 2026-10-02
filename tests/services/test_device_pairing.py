"""Device pairing: issue a code, redeem it, see and manage the shared clients.

Everything runs against the real SQLite index and the offline Walrus Memory
mock; only the model is faked, because the model is not what is under test.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from core.errors import DependencyUnavailableError
from models.entities.memory_index_model import INDEX_QUERY
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


class CountingRecallClient:
    """Counts the recovery reads so the once-per-process guard is observable."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.index_recalls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def recall(self, query: str, **kwargs: object):
        if query == INDEX_QUERY:
            self.index_recalls += 1
        return await self._inner.recall(query, **kwargs)  # type: ignore[attr-defined]


class SlowRememberClient:
    """Makes a settling write hang, so a blocking note copy is unmistakable."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def remember_and_wait(
        self,
        text: str,
        namespace: str | None = None,
        poll_interval_ms: int = 1500,
        timeout_ms: int = 60_000,
        idempotency_key: str | None = None,
    ):
        # A real note copy is the write that used to block. Index snapshots are
        # a separate, recovery-only write and must not make the test slow.
        if namespace and not namespace.endswith(".idx"):
            await asyncio.sleep(30)
        return await self._inner.remember_and_wait(  # type: ignore[attr-defined]
            text,
            namespace,
            poll_interval_ms=poll_interval_ms,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )


class FixedBlobClient:
    """Settles every write to one blob id, which is what linking causes.

    A note written once into the person's own namespace is written again into
    the shared namespace, and the relayer returns the same blob id. The local
    index must not try to hold two rows with that id.
    """

    BLOB_ID = "fixed-blob"

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def wait_for_remember_job(
        self, job_id: str, poll_interval_ms: int = 1500, timeout_ms: int = 60_000
    ):
        return SimpleNamespace(blob_id=self.BLOB_ID, namespace="ns", owner="owner")


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
    # The copy into the shared namespace is a background task so /pair returns
    # at once; wait for it before the paired client tries to recall.
    await harness.service.await_pending_writes()

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


# 11. Recovery is a relayer round trip and runs at most once per process.


async def test_recovery_runs_once_per_process_not_on_every_turn() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=CountingRecallClient,
    )

    for _ in range(3):
        await harness.say_settled("I am allergic to peanuts")

    assert harness.client.index_recalls == 1, (
        "the local index must be recovered once, not re-read on every turn"
    )


# 12. /pair never reads the index, so it does not pay for a recovery round trip.


async def test_pairing_skips_the_index_recovery_round_trip() -> None:
    harness = Harness([], client_wrapper=CountingRecallClient)

    reply = await harness.service.answer_command("/pair", "telegram", "42", "Ada")

    assert "pairing code" in reply
    assert harness.client.index_recalls == 0


# 13. Issuing a code returns at once even when a settling write would hang.


async def test_issuing_a_pairing_code_does_not_wait_for_note_copies() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=SlowRememberClient,
    )
    await harness.say_settled("I am allergic to peanuts")

    # Old code shipped every note with a blocking settle; against the live
    # relayer that is tens of seconds per note, which is the "sending forever"
    # the owner saw. The copy must be a background task now.
    reply = await asyncio.wait_for(
        harness.service.answer_command("/pair", "telegram", "42", "Ada"),
        timeout=1.0,
    )

    assert "copying 1 note" in reply
    await harness.service.await_pending_writes()


# 14. A relayer outage must not fail a command that only reads the local index.


async def test_a_command_still_answers_when_index_recovery_raises() -> None:
    # A local note exists, but this process has not yet recovered, exactly the
    # state right after a redeploy. Recovery is then attempted and fails.
    harness = Harness([])
    user = harness.users.get_or_create("telegram", "42", "Ada")
    harness.memories.create(
        user_id=user.id,
        blob_id="blob-peanuts",
        namespace="ranti.user.telegram-42",
        text="Ada is allergic to peanuts",
        importance=1.0,
        origin_surface="telegram",
        occurred_at="2026-01-01T00:00:00+00:00",
    )

    async def unreachable(*_args: object, **_kwargs: object) -> object:
        raise DependencyUnavailableError("walrus-memory", "relayer down")

    harness.service._memory.recall = unreachable  # type: ignore[method-assign]

    listing = await harness.service.answer_command("/memories", "telegram", "42", "Ada")
    assert "peanuts" in listing


# 15. Redeeming the same code twice on the SAME client is idempotent success.


async def test_redeeming_the_same_code_twice_on_one_client_is_not_an_error() -> None:
    harness = Harness([])
    code = await issue(harness, "telegram", "42", "Ada")

    first = await pair(harness, code, "cli", "kossi", "Ada")
    assert "paired with" in first

    # A retry after a transport timeout must not claim the client is not paired.
    second = await pair(harness, code, "cli", "kossi", "Ada")
    assert "already paired" in second
    assert "already used" not in second.lower()


# 16. A paired client lists and recalls the originator's memories, not just its own.


async def test_a_paired_client_lists_the_originators_memories() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    await harness.say_settled("I am allergic to peanuts", surface="telegram")
    code = await issue(harness, "telegram", "42", "Ada")
    await harness.service.await_pending_writes()
    await pair(harness, code, "cli", "kossi", "Ada")
    await harness.service.await_pending_writes()

    listing = await harness.service.answer_command("/memories", "cli", "kossi", "Ada")

    assert "peanuts" in listing, (
        "a paired client on one shared handle must see the whole memory space"
    )


# 17. Linking a note the local index already holds must not violate UNIQUE(blob_id).


async def test_linking_a_note_the_index_already_knows_does_not_raise() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        client_wrapper=FixedBlobClient,
    )
    first = await harness.say_settled("I am allergic to peanuts")

    reply = await harness.service.answer_command(
        "/link", "telegram", "42", "Ada", "ada-shared"
    )
    await harness.service.await_pending_writes()

    assert "copying 1 note" in reply
    rows = harness.memories.list_for_user(first.user_id, None, 100)
    # One row for the note, the real blob id, and no leftover placeholder from a
    # copy that could not be written because the id was already known.
    assert sum(1 for row in rows if row.blob_id == FixedBlobClient.BLOB_ID) == 1
    assert [row.blob_id for row in rows if row.blob_id.startswith("pending:")] == []
