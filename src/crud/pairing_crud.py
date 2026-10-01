"""Persistence for the pairing code entity. One entity, no joins.

Redemption is a single conditional UPDATE whose affected row count is the only
thing that decides the winner, so two simultaneous redemptions cannot both
succeed.
"""

from __future__ import annotations

import uuid

from core.database import Database
from models.entities.pairing_code_model import PairingCodeModel


def _row_to_model(row) -> PairingCodeModel:
    return PairingCodeModel(
        id=row["id"],
        user_id=row["user_id"],
        surface=row["surface"],
        surface_user_id=row["surface_user_id"],
        code_hash=row["code_hash"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        redeemed_at=row["redeemed_at"],
        redeemed_by_user_id=row["redeemed_by_user_id"],
        invalidated_at=row["invalidated_at"],
    )


class PairingCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, code_id: str) -> PairingCodeModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM pairing_codes WHERE id = ?", (code_id,)
        )
        return _row_to_model(row) if row else None

    def get_by_code_hash(self, code_hash: str) -> PairingCodeModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM pairing_codes WHERE code_hash = ?", (code_hash,)
        )
        return _row_to_model(row) if row else None

    def list_live_for_user(self, user_id: str) -> list[PairingCodeModel]:
        """Every unused, un-superseded code this identity issued, newest first."""
        rows = self._database.fetch_all(
            "SELECT * FROM pairing_codes WHERE user_id = ?"
            " AND redeemed_at IS NULL AND invalidated_at IS NULL"
            " ORDER BY created_at DESC",
            (user_id,),
        )
        return [_row_to_model(row) for row in rows]

    def create(
        self,
        user_id: str,
        surface: str,
        surface_user_id: str,
        code_hash: str,
        created_at: str,
        expires_at: str,
    ) -> PairingCodeModel:
        model = PairingCodeModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            surface=surface,
            surface_user_id=surface_user_id,
            code_hash=code_hash,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._database.execute(
            "INSERT INTO pairing_codes (id, user_id, surface, surface_user_id,"
            " code_hash, created_at, expires_at, redeemed_at, redeemed_by_user_id,"
            " invalidated_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
            (
                model.id,
                model.user_id,
                model.surface,
                model.surface_user_id,
                model.code_hash,
                model.created_at,
                model.expires_at,
            ),
        )
        return model

    def invalidate_live_for_user(self, user_id: str, invalidated_at: str) -> int:
        """Supersede every live code this identity issued. Returns rows changed."""
        return self._database.execute(
            "UPDATE pairing_codes SET invalidated_at = ?"
            " WHERE user_id = ? AND redeemed_at IS NULL AND invalidated_at IS NULL",
            (invalidated_at, user_id),
        )

    def redeem(
        self, code_id: str, redeemed_by_user_id: str, redeemed_at: str
    ) -> bool:
        """Claim a code exactly once.

        The condition is in the UPDATE itself and the affected row count is the
        verdict. A separate read-then-write would let two callers both see an
        unused code and both proceed.
        """
        changed = self._database.execute(
            "UPDATE pairing_codes SET redeemed_at = ?, redeemed_by_user_id = ?"
            " WHERE id = ? AND redeemed_at IS NULL AND invalidated_at IS NULL",
            (redeemed_at, redeemed_by_user_id, code_id),
        )
        return changed > 0
