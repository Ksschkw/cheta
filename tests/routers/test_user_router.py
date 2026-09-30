"""HTTP contract for the user routes, exercised through the real app factory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app(settings=Settings(database_path=":memory:", memwal_namespace_prefix="ranti"))
    with TestClient(app) as test_client:
        yield test_client


def test_register_returns_created_user_with_its_memory_namespace(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={"surface": "telegram", "surface_user_id": "77", "display_name": "Ada"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["surface_user_id"] == "77"
    assert body["memory_namespace"].endswith("telegram-77")


def test_registering_twice_returns_the_same_user_id(client: TestClient) -> None:
    payload = {"surface": "cli", "surface_user_id": "kooka", "display_name": "K"}
    first = client.post("/users", json=payload).json()
    second = client.post("/users", json=payload).json()

    assert first["id"] == second["id"]
    assert len(client.get("/users").json()) == 1


def test_unknown_user_returns_404_and_leaks_no_internals(client: TestClient) -> None:
    response = client.get("/users/missing-user")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"error", "detail"}
    assert body["error"] == "not_found"
    serialised = response.text.lower()
    for forbidden in ("traceback", "sqlite", "runtimeerror", "/home/"):
        assert forbidden not in serialised


def test_invalid_payload_is_rejected_before_the_service_runs(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={"surface": "telegram", "surface_user_id": "", "display_name": "Ada"},
    )

    assert response.status_code == 422


def test_delete_then_read_reports_gone(client: TestClient) -> None:
    created = client.post(
        "/users",
        json={"surface": "web", "surface_user_id": "widget-3", "display_name": "Guest"},
    ).json()

    assert client.delete(f"/users/{created['id']}").status_code == 204
    assert client.get(f"/users/{created['id']}").status_code == 404


def test_health_reports_mock_memory_when_credentials_are_absent(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["memory"]["mode"] == "mock"
    assert body["memory"]["degraded"] is False
