"""Timeout budget for the Walrus Memory write path.

A write is not a request: the relayer accepts a job and only reaches "done" once
the blob is persisted, which takes tens of seconds on the hosted relayer. The
first live integration run failed here with a 30 second boundary timeout, so the
relationship between the boundary budget and the SDK poll budget is pinned down.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.config import Settings
from core.container import build_memory_boundary
from core.gateways.memwal_gateway import MemWalGateway


@dataclass
class StoredResult:
    blob_id: str
    namespace: str
    owner: str


class RecordingClient:
    """Captures the poll budget and interval the gateway hands to the SDK."""

    def __init__(self) -> None:
        self.timeouts_ms: list[int] = []
        self.poll_intervals_ms: list[int] = []

    async def remember_and_wait(
        self,
        text: str,
        namespace: str | None = None,
        poll_interval_ms: int = 1500,
        timeout_ms: int = 60_000,
        idempotency_key: str | None = None,
    ) -> StoredResult:
        self.timeouts_ms.append(timeout_ms)
        self.poll_intervals_ms.append(poll_interval_ms)
        return StoredResult(blob_id="blob-1", namespace=namespace or "default", owner="owner-1")


def make_gateway(timeout_seconds: float) -> tuple[MemWalGateway, RecordingClient]:
    settings = Settings(database_path=":memory:", memwal_timeout_seconds=timeout_seconds)
    client = RecordingClient()
    gateway = MemWalGateway(
        client=client, boundary=build_memory_boundary(settings), mode="walrus"
    )
    return gateway, client


def test_the_default_write_budget_exceeds_the_sdk_poll_default() -> None:
    """The SDK defaults to 60s; a shorter boundary would mask its error."""
    settings = Settings(database_path=":memory:")

    assert settings.memwal_timeout_seconds > 60.0


def test_the_write_budget_is_configurable_from_the_environment() -> None:
    assert Settings.from_env({}).memwal_timeout_seconds == 90.0
    assert Settings.from_env({"MEMWAL_TIMEOUT_SECONDS": "120"}).memwal_timeout_seconds == 120.0
    # A malformed value falls back rather than crashing the app at startup.
    assert Settings.from_env({"MEMWAL_TIMEOUT_SECONDS": "soon"}).memwal_timeout_seconds == 90.0


async def test_the_sdk_poll_budget_expires_before_the_boundary_timeout() -> None:
    gateway, client = make_gateway(90.0)

    await gateway.remember("Ada is allergic to peanuts", "ranti.user.test")

    assert len(client.timeouts_ms) == 1
    poll_budget_ms = client.timeouts_ms[0]
    assert poll_budget_ms < 90_000, "the SDK must time out first to keep its job id"
    # A shorter budget is deliberate: fewer status calls against a relayer that
    # allows 60 weighted requests a minute.
    assert poll_budget_ms == 30_000
    # And the interval is longer, so those calls are spread out.
    assert client.poll_intervals_ms[0] >= 5000


async def test_a_short_boundary_still_leaves_the_sdk_a_workable_budget() -> None:
    gateway, client = make_gateway(35.0)

    await gateway.remember("Ada lives in Lagos", "ranti.user.test")

    assert client.timeouts_ms[0] == 25_000


async def test_a_timed_out_write_is_never_retried() -> None:
    """Retrying an accepted write would duplicate it: the relayer is append only."""
    settings = Settings(database_path=":memory:", memwal_timeout_seconds=0.01)
    calls = {"count": 0}

    class SlowClient:
        async def remember_and_wait(self, *args: object, **kwargs: object) -> StoredResult:
            calls["count"] += 1
            import asyncio

            await asyncio.sleep(5)
            return StoredResult("b", "n", "o")

    gateway = MemWalGateway(
        client=SlowClient(), boundary=build_memory_boundary(settings), mode="walrus"
    )

    from core.errors import DependencyUnavailableError

    with pytest.raises(DependencyUnavailableError):
        await gateway.remember("Ada plays the trumpet", "ranti.user.test")

    assert calls["count"] == 1
