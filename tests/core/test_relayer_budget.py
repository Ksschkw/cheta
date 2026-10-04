"""The relayer budget: one turn must not burn the per-minute allowance.

A single settle used to poll a remember job every 1.5s until an 80s budget ran
out, so one turn could spend most of the relayer's 60 weighted requests a minute
and the next turn was rate limited before it started. These tests pin the
reduced number of calls, the longer poll interval, the shorter budget, and the
rule that a 429 is never retried into the limit.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.container import build_memory_boundary
from core.config import Settings
from core.gateways.memwal_gateway import MemWalGateway
from core.resilience import Boundary, ResiliencePolicy, failure_fallback
from tests.routers.test_chat_and_memory_routers import build_test_container


class CountingGateway:
    """Counts the calls a turn makes through the memory gateway."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.recalls = 0
        self.submits = 0
        self.waits = 0

    async def recall(self, *args, **kwargs):
        self.recalls += 1
        return await self._inner.recall(*args, **kwargs)

    async def remember(self, *args, **kwargs):
        self.waits += 1
        return await self._inner.remember(*args, **kwargs)

    async def remember_accepted(self, *args, **kwargs):
        self.submits += 1
        return await self._inner.remember_accepted(*args, **kwargs)

    async def wait_for_remember(self, *args, **kwargs):
        self.waits += 1
        return await self._inner.wait_for_remember(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    @property
    def total(self) -> int:
        return self.recalls + self.submits + self.waits


async def test_one_turn_makes_at_most_six_relayer_calls() -> None:
    """The stated bound: three reads, two accepted writes, one settle wait."""
    container, _, _ = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    counting = CountingGateway(container.memory_gateway)
    container.conversation_service._memory = counting

    await container.conversation_service.handle_turn(
        "web", "u1", "Ada", "I am allergic to peanuts"
    )
    await container.conversation_service.await_pending_writes()

    assert counting.recalls == 3, counting.recalls
    assert counting.submits == 2, counting.submits
    # Only the accepted fact is waited on. The index snapshot is submitted and
    # not polled, so one turn has one settle cycle, not two.
    assert counting.waits == 1, counting.waits
    assert counting.total <= 6, f"a single turn made {counting.total} relayer calls"


@dataclass
class StoredResult:
    blob_id: str
    namespace: str
    owner: str


class RecordingWaitClient:
    """Records the poll interval and budget the gateway passes to the SDK."""

    def __init__(self) -> None:
        self.intervals: list[int] = []
        self.budgets: list[int] = []

    async def wait_for_remember_job(
        self,
        job_id: str,
        poll_interval_ms: int = 1500,
        timeout_ms: int = 60_000,
    ) -> StoredResult:
        self.intervals.append(poll_interval_ms)
        self.budgets.append(timeout_ms)
        return StoredResult(blob_id="blob-1", namespace="ranti.user.x", owner="owner-1")


async def test_a_settle_wait_polls_slowly_and_within_a_short_budget() -> None:
    settings = Settings(
        database_path=":memory:",
        memwal_timeout_seconds=90.0,
        memwal_poll_interval_ms=5000,
        memwal_settle_timeout_seconds=30.0,
    )
    client = RecordingWaitClient()
    gateway = MemWalGateway(
        client=client, boundary=build_memory_boundary(settings), mode="walrus"
    )

    await gateway.wait_for_remember("job-1")

    assert client.intervals == [5000]
    # 30s with a 5s base: the SDK's capped backoff makes at most four status
    # requests, against dozens before.
    assert client.budgets == [30_000]


class RelayerRateLimited(Exception):
    def __init__(self, retry_after: str) -> None:
        super().__init__("Walrus Memory API error (429): rate limit exceeded")
        self.status = 429
        self.retry_after = retry_after


async def test_a_relayer_429_is_not_retried_into_the_limit() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    boundary = Boundary(
        ResiliencePolicy(
            dependency="walrus-memory", timeout_seconds=5.0, max_attempts=3
        ),
        sleep=fake_sleep,
    )
    calls = {"count": 0}

    async def operation() -> None:
        calls["count"] += 1
        raise RelayerRateLimited("60")

    outcome = await boundary.call(
        operation, failure_fallback("walrus-memory", "read_failed"), idempotent=True
    )

    assert calls["count"] == 1, "a 429 was retried inside its own window"
    assert sleeps == [], "the boundary slept instead of honouring retry_after"
    assert outcome.ok is False
    assert "retry_after=60" in (outcome.error or "")


def test_a_plain_failure_is_still_retried() -> None:
    """Only a 429 is special-cased; ordinary faults keep their retries."""
    import asyncio

    async def exercise() -> int:
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        boundary = Boundary(
            ResiliencePolicy(
                dependency="walrus-memory", timeout_seconds=5.0, max_attempts=3
            ),
            sleep=fake_sleep,
        )
        calls = {"count": 0}

        async def operation() -> None:
            calls["count"] += 1
            raise RuntimeError("connection reset")

        await boundary.call(
            operation, failure_fallback("walrus-memory", "read_failed"), idempotent=True
        )
        return calls["count"]

    assert asyncio.run(exercise()) == 3
