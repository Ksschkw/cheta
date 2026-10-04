"""Persistence for webhook update ids already accepted.

Telegram re-delivers an update it did not see acknowledged in time. The id is
recorded before the work starts, and in the database rather than in memory, so a
re-delivery that arrives after a redeploy is still recognised. One insert is the
whole guarantee: the primary key makes the second claim a no-op.
"""

from __future__ import annotations

from core.database import Database


class SeenUpdateCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def claim(self, update_id: str, surface: str, seen_at: str) -> bool:
        """Record an update id. Returns True only for the first claim."""
        inserted = self._database.execute(
            "INSERT OR IGNORE INTO seen_updates (update_id, surface, seen_at) "
            "VALUES (?, ?, ?)",
            (update_id, surface, seen_at),
        )
        return inserted == 1

    def is_seen(self, update_id: str) -> bool:
        row = self._database.fetch_one(
            "SELECT 1 FROM seen_updates WHERE update_id = ?", (update_id,)
        )
        return row is not None

    def prune(self, seen_before: str) -> int:
        """Drop update ids older than the cutoff. Returns how many were removed."""
        return self._database.execute(
            "DELETE FROM seen_updates WHERE seen_at < ?", (seen_before,)
        )
