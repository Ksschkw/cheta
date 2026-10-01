"""Proactive reminder delivery.

This is the agent opening a conversation rather than answering one. A background
task, started from the application lifespan like the keep-alive pinger, polls the
reminder table and pushes anything that is due over the surface that stored it.

Two properties matter more than speed:

1. At most one delivery. Claiming a due reminder is a single atomic database
   transition, so two ticks that overlap cannot both send it.
2. Nothing is lost to a bad moment. If the push fails because the surface is
   unreachable, the reminder is released back to pending and the next tick tries
   again. Nothing is silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Callable

from core.protocols import ReplyChannelProtocol
from crud.reminder_crud import ReminderCrud

logger = logging.getLogger("ranti.reminder")

DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_BATCH_LIMIT = 25


class ReminderScheduler:
    """Delivers due reminders on an interval until stopped."""

    def __init__(
        self,
        reminders: ReminderCrud,
        reply_channel: ReplyChannelProtocol | None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._reminders = reminders
        self._reply_channel = reply_channel
        self._interval = max(5, interval_seconds)
        self._batch_limit = batch_limit
        self._now = now or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def deliver_due(self) -> int:
        """Run one tick. Returns how many reminders were delivered.

        Never raises: a scheduler that can crash the application it serves would
        be worse than no scheduler.
        """
        moment = self._now()
        due = self._reminders.claim_due(moment.isoformat(timespec="seconds"), self._batch_limit)
        delivered = 0
        for reminder in due:
            if self._reply_channel is None or not reminder.recipient_id:
                # No push transport on this deployment. The row goes back so it
                # is delivered if a transport is configured later, instead of
                # being marked sent when nothing was sent.
                self._reminders.release(reminder.id)
                continue
            try:
                await self._reply_channel.send_message(
                    reminder.recipient_id,
                    f"Reminder: {reminder.text}",
                )
            except asyncio.CancelledError:
                self._reminders.release(reminder.id)
                raise
            except Exception as error:  # noqa: BLE001 - one failure must not stop the rest
                logger.warning(
                    "reminder delivery failed id=%s error=%s",
                    reminder.id,
                    type(error).__name__,
                )
                self._reminders.release(reminder.id)
                continue
            self._reminders.mark_sent(reminder.id, moment.isoformat(timespec="seconds"))
            delivered += 1
        if delivered:
            logger.info("delivered %s reminder(s)", delivered)
        return delivered

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.deliver_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any one tick
                logger.warning("reminder tick failed", exc_info=True)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("reminder scheduler started interval=%ss", self._interval)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("reminder scheduler stopped")
