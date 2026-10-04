"""Background execution for Telegram webhook updates.

Telegram waits only a short while for a webhook response and re-delivers an
update it did not see acknowledged. A turn takes tens of seconds, so doing it
inline is what produced duplicate replies, replies that belonged to an earlier
message, and repeated failure notices: every re-delivery re-ran the whole turn.

The route therefore claims the update id durably and returns at once. This
runner does the turn afterwards, with three properties:

1. At most once per update id, across a restart, because the claim is a database
   row and not an in-process set.
2. In order per person. Two updates from one chat share a lock, so the second
   answer cannot overtake the first. Different people never share a lock.
3. Bounded concurrency. A burst of webhooks can leave many turns waiting, but
   only a fixed number execute at once.

A failed turn never disappears: the person gets the same honest unavailable
message the inline path used to send, exactly once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from crud.seen_update_crud import SeenUpdateCrud

logger = logging.getLogger("ranti.telegram.dispatch")

UpdateWork = Callable[[], Awaitable[None]]
FailureNotice = Callable[[], Awaitable[None]]


class _LoopState:
    """The semaphore and the per-key locks belonging to one event loop.

    An asyncio primitive binds to the loop that first waits on it. The
    application has one loop, but a test client can build a fresh one per
    request, so the state is kept per loop rather than for the process.
    """

    def __init__(self, max_concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, key: str) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock


class TelegramUpdateRunner:
    """Claims update ids once and runs their work in the background."""

    def __init__(
        self,
        seen_updates: SeenUpdateCrud,
        max_concurrency: int = 4,
        retention_seconds: float = 86400.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._seen = seen_updates
        self._max_concurrency = max(1, max_concurrency)
        self._retention = timedelta(seconds=max(0.0, retention_seconds))
        self._now = now or (lambda: datetime.now(UTC))
        self._states: dict[object, _LoopState] = {}

    def claim(self, update_id: str, surface: str = "telegram") -> bool:
        """Return True when this update id has not been accepted before.

        The insert and the prune are one quick database call each, done before
        the webhook answers, so two deliveries of one update cannot both pass.
        """
        moment = self._now()
        claimed = self._seen.claim(
            update_id, surface, moment.isoformat(timespec="seconds")
        )
        cutoff = (moment - self._retention).isoformat(timespec="seconds")
        self._seen.prune(cutoff)
        return claimed

    def _state(self) -> _LoopState:
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _LoopState(self._max_concurrency)
            self._states[loop] = state
        return state

    async def run(
        self,
        key: str,
        work: UpdateWork,
        on_failure: FailureNotice | None = None,
    ) -> None:
        """Run one accepted update in the background, serialised per key.

        The key is the person's chat id. Holding the per-key lock across the
        semaphore keeps one person's turns in order without letting a queue for
        one person starve everyone else.
        """
        state = self._state()
        lock = state.lock_for(key)
        async with lock, state.semaphore:
            try:
                await work()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a background failure must be told
                logger.warning(
                    "telegram update failed key=%s error=%s", key, type(error).__name__
                )
                if on_failure is None:
                    return
                try:
                    await on_failure()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the notice itself may fail
                    logger.error(
                        "could not tell the person their turn failed key=%s",
                        key,
                        exc_info=True,
                    )
