"""Resilience primitives at a real outbound boundary."""

from __future__ import annotations

import asyncio

import pytest

from core.resilience import (
    Boundary,
    BreakerState,
    Outcome,
    ResiliencePolicy,
    failure_fallback,
    outcome_fallback,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def increment(self, name: str, tags: dict[str, str]) -> None:
        self.events.append((name, tags))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


async def _no_sleep(_: float) -> None:
    return None


def make_policy(**overrides: object) -> ResiliencePolicy:
    base = {
        "dependency": "upstream",
        "timeout_seconds": 0.05,
        "failure_threshold": 2,
        "reset_timeout_seconds": 0.01,
        "max_attempts": 3,
        "base_backoff_seconds": 0.0,
        "max_backoff_seconds": 0.0,
        "max_concurrency": 2,
    }
    base.update(overrides)
    return ResiliencePolicy(**base)  # type: ignore[arg-type]


async def test_read_retries_with_jitter_and_reports_attempt_count() -> None:
    calls = {"count": 0}

    async def flaky() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("transient")
        return "recovered"

    boundary = Boundary(make_policy(), RecordingSink(), sleep=_no_sleep)
    outcome = await boundary.call(flaky, failure_fallback("upstream"), idempotent=True)

    assert outcome.ok is True
    assert outcome.value == "recovered"
    assert outcome.attempts == 3
    assert calls["count"] == 3


async def test_write_is_attempted_once_even_when_retries_are_configured() -> None:
    calls = {"count": 0}

    async def always_fails() -> str:
        calls["count"] += 1
        raise RuntimeError("relayer refused")

    boundary = Boundary(make_policy(), RecordingSink(), sleep=_no_sleep)
    outcome = await boundary.call(always_fails, failure_fallback("upstream"), idempotent=False)

    assert calls["count"] == 1
    assert outcome.ok is False
    assert outcome.degraded is False
    assert outcome.value is None
    assert outcome.error is not None and "relayer refused" in outcome.error


async def test_timeout_produces_typed_degraded_value_never_null() -> None:
    async def too_slow() -> str:
        await asyncio.sleep(5)

    boundary = Boundary(make_policy(timeout_seconds=0.01), RecordingSink(), sleep=_no_sleep)
    outcome = await boundary.call(
        too_slow, outcome_fallback("upstream", "cached-empty"), idempotent=True
    )

    assert outcome.degraded is True
    assert outcome.value == "cached-empty"
    assert outcome.error is not None and "timeout" in outcome.error


async def test_breaker_opens_short_circuits_then_half_opens_and_closes() -> None:
    sink = RecordingSink()
    boundary = Boundary(make_policy(), sink, sleep=_no_sleep)
    invoked = {"probe": 0}

    async def down() -> str:
        raise RuntimeError("down")

    async def probe() -> str:
        invoked["probe"] += 1
        return "healthy"

    for _ in range(2):
        await boundary.call(down, failure_fallback("upstream"), idempotent=False)
    assert boundary.breaker.state is BreakerState.OPEN

    short_circuited = await boundary.call(probe, failure_fallback("upstream"), idempotent=False)
    assert invoked["probe"] == 0
    assert short_circuited.ok is False
    assert "circuit_open" in (short_circuited.error or "")

    await asyncio.sleep(0.02)
    recovered = await boundary.call(probe, failure_fallback("upstream"), idempotent=False)

    assert invoked["probe"] == 1
    assert recovered.ok is True
    assert boundary.breaker.state is BreakerState.CLOSED
    assert "breaker.state_change" in sink.names()


async def test_bulkhead_caps_concurrency_per_dependency() -> None:
    policy = make_policy(max_concurrency=1)
    boundary = Boundary(policy, RecordingSink(), sleep=_no_sleep)
    inside = {"current": 0, "peak": 0}

    async def slow() -> str:
        inside["current"] += 1
        inside["peak"] = max(inside["peak"], inside["current"])
        await asyncio.sleep(0.01)
        inside["current"] -= 1
        return "done"

    results = await asyncio.gather(
        boundary.call(slow, failure_fallback("upstream"), idempotent=True),
        boundary.call(slow, failure_fallback("upstream"), idempotent=True),
        boundary.call(slow, failure_fallback("upstream"), idempotent=True),
    )

    assert inside["peak"] == 1
    assert all(isinstance(result, Outcome) and result.ok for result in results)
