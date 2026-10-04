"""Resilience primitives for outbound boundaries only.

Circuit breakers, bulkheads, timeouts and jittered retries live here and are
injected from the composition root. Nothing in ``services`` constructs them, and
nothing in this module is applied to an in-process call between our own layers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar

T = TypeVar("T")

logger = logging.getLogger("ranti.resilience")


def http_status(error: BaseException) -> int | None:
    """The HTTP status an outbound exception carries, when it has one."""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(error, "status", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def rate_limit_retry_after(error: BaseException) -> float | None:
    """The server's Retry-After when ``error`` is an HTTP 429, otherwise None.

    A rate limit is not a transient blip to retry immediately: the server has
    already said how long to wait. Retrying inside that window spends the budget
    that is being limited, so a 429 must be surfaced to the failover or the
    degraded path at once, carrying the server's own number when it gives one.
    """
    if http_status(error) != 429:
        return None
    raw = getattr(error, "retry_after", None)
    if raw is None:
        raw = getattr(error, "retry_after_seconds", None)
    if raw is None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if headers is not None:
            raw = headers.get("retry-after")
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return None


class MetricSink(Protocol):
    def increment(self, name: str, tags: dict[str, str]) -> None: ...


class StructuredLogMetricSink:
    """Default sink: emits one structured log line per metric."""

    def __init__(self, component: str) -> None:
        self._component = component

    def increment(self, name: str, tags: dict[str, str]) -> None:
        logger.warning(
            json.dumps(
                {"metric": name, "component": self._component, "tags": tags},
                sort_keys=True,
            )
        )


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class ResiliencePolicy:
    """One policy per outbound dependency, not per call site."""

    dependency: str
    timeout_seconds: float = 15.0
    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0
    half_open_probes: int = 1
    max_attempts: int = 3
    base_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 4.0
    max_concurrency: int = 4
    retry_writes: bool = False


class CircuitBreaker:
    """Closed, open, half-open. One instance per outbound dependency."""

    def __init__(self, policy: ResiliencePolicy, metrics: MetricSink) -> None:
        self._policy = policy
        self._metrics = metrics
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probes_in_flight = 0

    @property
    def state(self) -> BreakerState:
        return self._state

    def _transition(self, new_state: BreakerState, reason: str) -> None:
        if new_state is self._state:
            return
        previous = self._state
        self._state = new_state
        self._metrics.increment(
            "breaker.state_change",
            {
                "dependency": self._policy.dependency,
                "from": previous.value,
                "to": new_state.value,
                "reason": reason,
            },
        )

    def allows_request(self) -> bool:
        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self._policy.reset_timeout_seconds:
                self._transition(BreakerState.HALF_OPEN, "reset_timeout_elapsed")
                self._probes_in_flight = 0
                return True
            return False
        if self._probes_in_flight < self._policy.half_open_probes:
            self._probes_in_flight += 1
            return True
        return False

    def on_success(self) -> None:
        self._consecutive_failures = 0
        self._probes_in_flight = 0
        if self._state is BreakerState.HALF_OPEN:
            self._transition(BreakerState.CLOSED, "probe_succeeded")

    def on_failure(self) -> None:
        self._probes_in_flight = 0
        if self._state is BreakerState.HALF_OPEN:
            self._opened_at = time.monotonic()
            self._transition(BreakerState.OPEN, "probe_failed")
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._policy.failure_threshold:
            self._opened_at = time.monotonic()
            self._transition(BreakerState.OPEN, "failure_threshold_reached")


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """Typed result of a boundary call. A fallback is always explicit."""

    ok: bool
    value: T | None = None
    error: str | None = None
    degraded: bool = False
    attempts: int = 1
    dependency: str = ""

    def unwrap(self) -> T:
        if not self.ok or self.value is None:
            raise DependencyUnavailableError(self.dependency, self.error or "call failed")
        return self.value


class Bulkhead:
    """Dedicated concurrency limit per dependency."""

    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._semaphore:
            return await operation()


class Boundary:
    """Timeout, breaker, bulkhead and bounded jittered retry around one dependency."""

    def __init__(
        self,
        policy: ResiliencePolicy,
        metrics: MetricSink | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._policy = policy
        self._metrics = metrics or StructuredLogMetricSink(policy.dependency)
        self._breaker = CircuitBreaker(policy, self._metrics)
        self._bulkhead = Bulkhead(policy.max_concurrency)
        self._sleep = sleep

    @property
    def policy(self) -> ResiliencePolicy:
        return self._policy

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def _backoff(self, attempt: int) -> float:
        ceiling = min(
            self._policy.max_backoff_seconds,
            self._policy.base_backoff_seconds * (2 ** (attempt - 1)),
        )
        return random.uniform(0.0, ceiling)

    async def call(
        self,
        operation: Callable[[], Awaitable[T]],
        fallback: Callable[[str], Awaitable[Outcome[T]]],
        *,
        idempotent: bool,
    ) -> Outcome[T]:
        attempts_allowed = self._policy.max_attempts if idempotent else 1

        if not self._breaker.allows_request():
            self._metrics.increment(
                "breaker.fallback", {"dependency": self._policy.dependency, "reason": "circuit_open"}
            )
            return await fallback("circuit_open")

        last_error = "unknown"
        for attempt in range(1, attempts_allowed + 1):
            rate_limited = False
            try:
                async with asyncio.timeout(self._policy.timeout_seconds):
                    value = await self._bulkhead.run(operation)
                self._breaker.on_success()
                return Outcome(
                    ok=True,
                    value=value,
                    attempts=attempt,
                    dependency=self._policy.dependency,
                )
            except asyncio.TimeoutError:
                last_error = f"timeout after {self._policy.timeout_seconds}s"
            except Exception as exc:  # noqa: BLE001 - boundary converts everything
                if http_status(exc) == 429:
                    rate_limited = True
                    delay = rate_limit_retry_after(exc)
                    suffix = f" retry_after={delay:g}s" if delay is not None else ""
                    last_error = f"rate_limited{suffix}: {exc}"
                else:
                    last_error = f"{type(exc).__name__}: {exc}"

            self._breaker.on_failure()
            # A 429 is never retried here: the server told us how long to wait,
            # and waiting would only deepen the limit. The caller decides whether
            # to fail over or degrade.
            if rate_limited:
                break
            if attempt < attempts_allowed:
                await self._sleep(self._backoff(attempt))

        self._metrics.increment(
            "breaker.fallback",
            {"dependency": self._policy.dependency, "reason": last_error},
        )
        return await fallback(last_error)


@dataclass
class DegradedTrip:
    """Recorded in tests to prove a fallback actually ran."""

    dependency: str
    reason: str = ""
    tags: dict[str, str] = field(default_factory=dict)


def outcome_fallback(
    dependency: str, value: T, reason_prefix: str = "degraded"
) -> Callable[[str], Awaitable[Outcome[T]]]:
    """Build a fallback that returns an explicit degraded value, never a silent null."""

    async def _fallback(reason: str) -> Outcome[T]:
        return Outcome(
            ok=False,
            value=value,
            error=f"{reason_prefix}: {reason}",
            degraded=True,
            dependency=dependency,
        )

    return _fallback


def failure_fallback(
    dependency: str, reason_prefix: str = "unavailable"
) -> Callable[[str], Awaitable[Outcome[Any]]]:
    """Build a fallback that reports failure with no value and no silent null."""

    async def _fallback(reason: str) -> Outcome[Any]:
        return Outcome(
            ok=False,
            value=None,
            error=f"{reason_prefix}: {reason}",
            degraded=False,
            dependency=dependency,
        )

    return _fallback
