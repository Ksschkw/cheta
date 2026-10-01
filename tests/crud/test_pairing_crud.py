"""Persistence contract for pairing codes, including atomic redemption."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.database import Database
from crud.pairing_crud import PairingCrud

CREATED = "2025-01-01T00:00:00+00:00"
EXPIRES = "2025-01-01T00:10:00+00:00"
NOW = "2025-01-01T00:01:00+00:00"


@pytest.fixture
def crud() -> PairingCrud:
    database = Database(":memory:")
    database.migrate()
    return PairingCrud(database)


def test_create_then_read_back_by_hash(crud: PairingCrud) -> None:
    created = crud.create("u1", "telegram", "42", "hash-a", CREATED, EXPIRES)

    fetched = crud.get_by_code_hash("hash-a")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.surface_user_id == "42"
    assert fetched.redeemed_at is None
    assert fetched.redeemed_by_user_id is None
    assert fetched.invalidated_at is None
    assert crud.get_by_code_hash("no-such-hash") is None


def test_redeem_only_claims_the_code_once(crud: PairingCrud) -> None:
    created = crud.create("u1", "telegram", "42", "hash-a", CREATED, EXPIRES)

    assert crud.redeem(created.id, "u2", NOW) is True
    assert crud.redeem(created.id, "u3", NOW) is False
    claimed = crud.get_by_id(created.id)
    assert claimed is not None and claimed.redeemed_by_user_id == "u2"


def test_two_simultaneous_redemptions_produce_exactly_one_winner(
    crud: PairingCrud,
) -> None:
    created = crud.create("u1", "telegram", "42", "hash-a", CREATED, EXPIRES)

    def claim(who: str) -> bool:
        return crud.redeem(created.id, who, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("cli", "web")))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_invalidating_live_codes_leaves_a_redeemed_one_alone(
    crud: PairingCrud,
) -> None:
    live = crud.create("u1", "telegram", "42", "hash-a", CREATED, EXPIRES)
    used = crud.create("u1", "telegram", "42", "hash-b", CREATED, EXPIRES)
    assert crud.redeem(used.id, "u2", NOW) is True

    changed = crud.invalidate_live_for_user("u1", NOW)

    assert changed == 1
    replaced = crud.get_by_id(live.id)
    assert replaced is not None and replaced.invalidated_at == NOW
    assert crud.redeem(live.id, "u3", NOW) is False
    assert crud.list_live_for_user("u1") == []
