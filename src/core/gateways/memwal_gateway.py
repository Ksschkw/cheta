"""Walrus Memory gateway: the only place the memwal SDK is touched."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from core.errors import DependencyUnavailableError
from core.resilience import Boundary, Outcome, failure_fallback, outcome_fallback
from schemas.memory_schema import (
    AcceptedMemorySchema,
    ExtractedFactSchema,
    MemoryHealthSchema,
    NamespaceListingSchema,
    NamespaceSummarySchema,
    RecallOutcomeSchema,
    RecalledMemorySchema,
    RestoreReportSchema,
    StoredMemorySchema,
)

logger = logging.getLogger("ranti.gateway.memwal")

T = TypeVar("T")


class MemWalGateway:
    """Wraps whichever memwal client is injected (live relayer or offline mock).

    Reads are retried with jittered backoff because they are idempotent. Writes
    are never blind-retried: an accepted write that times out may already be on
    Walrus, and the relayer is append-only, so a retry would duplicate the
    memory. Writes carry a deterministic idempotency key instead.
    """

    def __init__(
        self,
        client: Any,
        boundary: Boundary,
        mode: str,
        poll_interval_ms: int = 5000,
        settle_timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._boundary = boundary
        self._mode = mode
        # The relayer allows 60 weighted requests a minute. A short interval over
        # a long budget spent that on a single settle, so the next turn was rate
        # limited before it started. Fewer, further-apart status calls.
        self._poll_interval_ms = max(1000, poll_interval_ms)
        self._settle_timeout_seconds = max(5.0, settle_timeout_seconds)
        self._degradation_count = 0
        self._request_count = 0
        self._last_error: str | None = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def degraded(self) -> bool:
        """True once any call has been served by a fallback since process start."""
        return self._degradation_count > 0

    @property
    def request_count(self) -> int:
        """Every boundary call made since process start, so /health can show it.

        This is the number that matters for the relayer's rate limit: a turn
        that calls the relayer several times is the turn that trips a 429.
        """
        return self._request_count

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def _read(
        self,
        operation: Callable[[], Awaitable[T]],
        fallback: Callable[[str], Awaitable[Outcome[T]]],
    ) -> T:
        self._request_count += 1
        outcome = await self._boundary.call(operation, fallback, idempotent=True)
        return self._resolve(outcome)

    async def _write(self, operation: Callable[[], Awaitable[T]]) -> T:
        fallback = failure_fallback("walrus-memory", "write_failed")
        self._request_count += 1
        outcome = await self._boundary.call(operation, fallback, idempotent=False)
        return self._resolve(outcome)

    def _resolve(self, outcome: Outcome[T]) -> T:
        if outcome.degraded:
            self._degradation_count += 1
            self._last_error = outcome.error
            if outcome.value is None:
                raise DependencyUnavailableError(
                    "walrus-memory", outcome.error or "degraded with no fallback value"
                )
            return outcome.value
        if not outcome.ok:
            self._last_error = outcome.error
            raise DependencyUnavailableError(
                "walrus-memory", outcome.error or "call failed"
            )
        assert outcome.value is not None
        return outcome.value

    async def health(self) -> MemoryHealthSchema:
        async def operation() -> MemoryHealthSchema:
            result = await self._client.health()
            return MemoryHealthSchema(
                status=result.status,
                version=result.version,
                write_ready=bool(getattr(result, "write_ready", True)),
            )

        degraded = MemoryHealthSchema(status="degraded", version="unavailable", write_ready=False)
        return await self._read(operation, outcome_fallback("walrus-memory", degraded))

    def _poll_budget_ms(self) -> int:
        """The SDK's own poll budget, kept inside the boundary timeout.

        The SDK must expire before the boundary so that the error surfaced is
        its precise one (job timeout, with the job id) rather than a generic
        boundary timeout with no handle. The budget is also capped by the
        settle timeout, because a longer wait means more status calls against a
        relayer that rate limits by the minute.
        """
        boundary_budget = self._boundary.policy.timeout_seconds - 10.0
        budget = min(self._settle_timeout_seconds, max(5.0, boundary_budget))
        return int(budget * 1000)

    async def remember(
        self, text: str, namespace: str, idempotency_key: str | None = None
    ) -> StoredMemorySchema:
        # A write is accepted as a job and only reaches "done" once the blob is
        # uploaded and indexed, which on the hosted relayer takes tens of
        # seconds. Prefer :meth:`remember_accepted` on a latency-sensitive path:
        # this method blocks the caller until the job settles.
        poll_budget_ms = self._poll_budget_ms()

        async def operation() -> StoredMemorySchema:
            result = await self._client.remember_and_wait(
                text,
                namespace,
                poll_interval_ms=self._poll_interval_ms,
                timeout_ms=poll_budget_ms,
                idempotency_key=idempotency_key,
            )
            return StoredMemorySchema(
                blob_id=result.blob_id,
                namespace=result.namespace,
                owner=result.owner,
            )

        return await self._write(operation)

    async def remember_accepted(
        self, text: str, namespace: str, idempotency_key: str | None = None
    ) -> AcceptedMemorySchema:
        """Submit a write and return the job id as soon as the relayer accepts.

        This is not a blind retry: it runs through the same non-idempotent write
        boundary as :meth:`remember`, and the idempotency key is passed through
        unchanged, so an accepted write is never duplicated.
        """

        async def operation() -> AcceptedMemorySchema:
            result = await self._client.remember(
                text,
                namespace,
                idempotency_key=idempotency_key,
            )
            return AcceptedMemorySchema(
                job_id=result.job_id,
                status=getattr(result, "status", "pending"),
            )

        return await self._write(operation)

    async def wait_for_remember(self, job_id: str) -> StoredMemorySchema:
        """Poll an accepted job until it settles. Reading a job is idempotent."""
        poll_budget_ms = self._poll_budget_ms()

        async def operation() -> StoredMemorySchema:
            result = await self._client.wait_for_remember_job(
                job_id,
                poll_interval_ms=self._poll_interval_ms,
                timeout_ms=poll_budget_ms,
            )
            return StoredMemorySchema(
                blob_id=result.blob_id,
                namespace=result.namespace,
                owner=result.owner,
            )

        return await self._read(
            operation, failure_fallback("walrus-memory", "settle_failed")
        )

    async def recall(
        self,
        query: str,
        namespace: str,
        limit: int = 25,
        max_distance: float | None = None,
    ) -> RecallOutcomeSchema:
        async def operation() -> RecallOutcomeSchema:
            result = await self._client.recall(
                query, limit=limit, namespace=namespace, max_distance=max_distance
            )
            return RecallOutcomeSchema(
                memories=tuple(
                    RecalledMemorySchema(
                        blob_id=memory.blob_id, text=memory.text, distance=memory.distance
                    )
                    for memory in result.results
                )
            )

        async def degraded(reason: str) -> Outcome[RecallOutcomeSchema]:
            return Outcome(
                ok=False,
                value=RecallOutcomeSchema(memories=(), degraded=True, error=reason),
                error=reason,
                degraded=True,
                dependency="walrus-memory",
            )

        return await self._read(operation, degraded)

    async def embed(self, text: str) -> Sequence[float]:
        async def operation() -> Sequence[float]:
            result = await self._client.embed(text)
            return tuple(result.vector)

        return await self._read(operation, outcome_fallback("walrus-memory", ()))

    async def list_namespaces(self, limit: int = 100) -> NamespaceListingSchema:
        async def operation() -> NamespaceListingSchema:
            result = await self._client.list_namespaces(limit=limit)
            return NamespaceListingSchema(
                namespaces=tuple(
                    NamespaceSummarySchema(
                        name=summary.name,
                        memory_count=summary.memory_count,
                        storage_used=summary.storage_used,
                        updated_at=summary.updated_at,
                    )
                    for summary in result.namespaces
                )
            )

        async def degraded(reason: str) -> Outcome[NamespaceListingSchema]:
            return Outcome(
                ok=False,
                value=NamespaceListingSchema(namespaces=(), degraded=True, error=reason),
                error=reason,
                degraded=True,
                dependency="walrus-memory",
            )

        return await self._read(operation, degraded)

    async def restore(self, namespace: str, limit: int = 50) -> RestoreReportSchema:
        async def operation() -> RestoreReportSchema:
            result = await self._client.restore(namespace, limit)
            return RestoreReportSchema(
                namespace=result.namespace,
                restored=result.restored,
                skipped=result.skipped,
                failed=getattr(result, "failed", 0),
                total=result.total,
                truncated=getattr(result, "truncated", False),
            )

        return await self._read(operation, failure_fallback("walrus-memory", "restore_failed"))

    async def analyze(
        self, text: str, namespace: str, occurred_at: str | None = None
    ) -> tuple[ExtractedFactSchema, ...]:
        """Relayer-side fact extraction. Kept for the extraction comparison only.

        The hot path extracts facts with our own model so that duplicates can be
        rejected before the append-only write happens.
        """

        async def operation() -> tuple[ExtractedFactSchema, ...]:
            result = await self._client.analyze(text, namespace, occurred_at=occurred_at)
            return tuple(
                ExtractedFactSchema(text=fact.text, blob_id=getattr(fact, "blob_id", None))
                for fact in result.facts
            )

        return await self._read(operation, outcome_fallback("walrus-memory", ()))
