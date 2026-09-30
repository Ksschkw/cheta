"""Live round trip against the hosted Walrus Memory relayer.

Skipped unless RANTI_LIVE_TESTS=1 is set and the Walrus Memory credentials are
available. Credentials are read from the process environment first and from
``.env`` second, so putting them in ``.env`` is enough:

    RANTI_LIVE_TESTS=1 .venv/bin/python -m pytest tests/integration -q

This is the only test that proves the real integration works end to end. The
offline suite proves the logic; this proves the wiring. It writes one small
memory into a dedicated verification namespace, never a user's memory space.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_NAMESPACE = "ranti.verify"


def load_credentials() -> dict[str, str]:
    """Process environment wins; ``.env`` fills the gaps.

    Reading ``.env`` here matters: without it a developer who puts the keys in
    ``.env`` (which is what the README tells them to do) sees this test skip and
    reasonably concludes the integration is untested.
    """
    values: dict[str, str] = {}
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    values.update(os.environ)
    return values


ENVIRONMENT = load_credentials()

LIVE_ENABLED = (
    ENVIRONMENT.get("RANTI_LIVE_TESTS") == "1"
    and bool(ENVIRONMENT.get("MEMWAL_PRIVATE_KEY"))
    and bool(ENVIRONMENT.get("MEMWAL_ACCOUNT_ID"))
)

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason=(
        "live relayer test: set RANTI_LIVE_TESTS=1 and provide MEMWAL_PRIVATE_KEY "
        "and MEMWAL_ACCOUNT_ID, either in the environment or in .env"
    ),
)


@pytest.mark.asyncio
async def test_a_memory_can_be_written_and_read_back_through_the_real_relayer() -> None:
    from memwal import MemWal

    client = MemWal.create(
        key=ENVIRONMENT["MEMWAL_PRIVATE_KEY"],
        account_id=ENVIRONMENT["MEMWAL_ACCOUNT_ID"],
        server_url=ENVIRONMENT.get(
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
    assert stored.owner

    recalled = await client.recall("Ranti live test marker", limit=10, namespace=LIVE_NAMESPACE)
    hits = {memory.blob_id: memory for memory in recalled.results}

    assert stored.blob_id in hits, (
        f"wrote {stored.blob_id} but recall returned {sorted(hits)}; "
        "indexing can lag, so rerun once before treating this as a failure"
    )
    assert hits[stored.blob_id].distance < 0.5

    await client.close()


@pytest.mark.asyncio
async def test_the_app_container_uses_the_real_client_when_credentials_exist() -> None:
    """Guards the wiring, not the SDK: the app must not silently stay on the mock."""
    from core.config import Settings
    from core.container import build_memory_gateway

    settings = Settings.from_env(ENVIRONMENT)
    assert settings.memwal_configured is True

    gateway = build_memory_gateway(settings)

    assert gateway.mode == "walrus"
    health = await gateway.health()
    assert health.status == "ok"
    assert gateway.degraded is False


@pytest.mark.asyncio
async def test_the_real_relayer_sees_the_users_own_namespace() -> None:
    """Namespaces are the isolation boundary; prove the app derives and uses one."""
    from core.config import Settings
    from core.container import build_memory_gateway

    settings = Settings.from_env(ENVIRONMENT)
    gateway = build_memory_gateway(settings)

    namespace = settings.memory_namespace("integration-test-user")
    stored = await gateway.remember(f"Namespace isolation probe {uuid.uuid4()}", namespace)
    assert stored.namespace == namespace

    listing = await gateway.list_namespaces(limit=500)
    assert listing.degraded is False
    assert namespace in {summary.name for summary in listing.namespaces}

    outcome = await gateway.recall("Namespace isolation probe", namespace, limit=5)

    # The store is append-only, so re-running this test leaves the previous
    # probes in place and recall legitimately returns them too. Containment is
    # the correct assertion; an exact list would only pass on a virgin account.
    blob_ids = [memory.blob_id for memory in outcome.memories]
    assert stored.blob_id in blob_ids, f"wrote {stored.blob_id} but recall returned {blob_ids}"

    # And nothing from a different namespace may leak in.
    other_namespace = settings.memory_namespace("integration-test-other-user")
    other = await gateway.recall("Namespace isolation probe", other_namespace, limit=5)
    assert stored.blob_id not in {memory.blob_id for memory in other.memories}
