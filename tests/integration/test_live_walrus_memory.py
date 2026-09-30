"""Live round trip against the hosted Walrus Memory relayer.

Skipped unless both the credentials and RANTI_LIVE_TESTS=1 are present, so the
default test run stays hermetic and offline. When the credentials land, run:

    RANTI_LIVE_TESTS=1 .venv/bin/python -m pytest tests/integration -q

This is the only test that proves the real integration works end to end. The
offline suite proves the logic; this proves the wiring.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("RANTI_LIVE_TESTS") == "1"
        and os.environ.get("MEMWAL_PRIVATE_KEY")
        and os.environ.get("MEMWAL_ACCOUNT_ID")
    ),
    reason=(
        "live relayer test: set RANTI_LIVE_TESTS=1 and provide MEMWAL_PRIVATE_KEY "
        "and MEMWAL_ACCOUNT_ID to run it"
    ),
)

LIVE_NAMESPACE = "ranti.verify"


@pytest.mark.asyncio
async def test_a_memory_can_be_written_and_read_back_through_the_real_relayer() -> None:
    from memwal import MemWal

    client = MemWal.create(
        key=os.environ["MEMWAL_PRIVATE_KEY"],
        account_id=os.environ["MEMWAL_ACCOUNT_ID"],
        server_url=os.environ.get(
            "MEMWAL_SERVER_URL", "https://relayer.memory.walrus.xyz"
        ),
        namespace=LIVE_NAMESPACE,
    )

    health = await client.health()
    assert health.status == "ok"
    assert getattr(health, "write_ready", True) is not False

    marker = f"Ranti live test marker {uuid.uuid4()}"
    stored = await client.remember_and_wait(
        marker,
        LIVE_NAMESPACE,
        timeout_ms=90_000,
        idempotency_key=f"ranti-live-{uuid.uuid4()}",
    )
    assert stored.blob_id
    assert stored.namespace == LIVE_NAMESPACE

    recalled = await client.recall("Ranti live test marker", limit=10, namespace=LIVE_NAMESPACE)
    blob_ids = {memory.blob_id for memory in recalled.results}

    assert stored.blob_id in blob_ids, (
        f"wrote {stored.blob_id} but recall returned {sorted(blob_ids)}; "
        "indexing can lag, so rerun once before treating this as a failure"
    )

    await client.close()


@pytest.mark.asyncio
async def test_the_app_container_uses_the_real_client_when_credentials_exist() -> None:
    """Guards the wiring, not the SDK: the app must not silently stay on the mock."""
    from core.config import Settings
    from core.container import build_memory_gateway

    settings = Settings.from_env(os.environ)
    assert settings.memwal_configured is True

    gateway = build_memory_gateway(settings)

    assert gateway.mode == "walrus"
    health = await gateway.health()
    assert health.status == "ok"
