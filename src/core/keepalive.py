"""Keep-alive for a free host that idles out.

Render's free web services spin down after roughly 15 minutes with no inbound
traffic, and the next request pays a cold start of about a minute. Telegram
retries a webhook delivery that times out, so a sleeping bot is slow rather than
broken, but slow enough to look broken to a real person.

This pinger makes an inbound-shaped request to the service's own public URL on a
fixed interval, which is what Render counts as activity. It is opt-in because it
holds the instance awake, and one always-on free service already consumes
essentially the whole monthly instance-hour allowance.

It is deliberately forgiving: a failure is logged and ignored. A keep-alive that
can crash the application it is keeping alive would be worse than no keep-alive.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import httpx

logger = logging.getLogger("ranti.keepalive")

DEFAULT_INTERVAL_SECONDS = 10 * 60
PING_TIMEOUT_SECONDS = 25.0


class KeepAlivePinger:
    """Pings the service's own /health on an interval until stopped."""

    def __init__(
        self,
        base_url: str,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("a keep-alive target must be an https URL")
        self._url = f"{base_url.rstrip('/')}/health"
        self._interval = max(60, interval_seconds)
        self._client_factory = client_factory or httpx.AsyncClient
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def ping_once(self) -> int | None:
        """Return the status code, or None when the ping failed."""
        try:
            async with self._client_factory(timeout=PING_TIMEOUT_SECONDS) as client:
                response = await client.get(self._url)
            return response.status_code
        except Exception as error:  # noqa: BLE001 - keep-alive must not raise
            logger.warning("keep-alive ping failed: %s: %s", type(error).__name__, error)
            return None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.ping_once()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("keep-alive started interval=%ss url=%s", self._interval, self._url)

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
        logger.info("keep-alive stopped")
