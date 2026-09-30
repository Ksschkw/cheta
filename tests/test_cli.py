"""CLI tests. No live server: a stubbed transport exercises real code paths."""

from __future__ import annotations

import json
import stat

import httpx
import pytest

from cli import main

BASE = "http://api.test:8000"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Point the CLI at a throwaway identity file and a fake API URL."""
    config = tmp_path / "ranti_cli.json"
    monkeypatch.setenv("RANTI_CLI_CONFIG", str(config))
    monkeypatch.setenv("RANTI_API_URL", BASE)
    return config


def make_transport(handler):
    return httpx.MockTransport(handler)


def user_response():
    return httpx.Response(
        201,
        json={
            "id": "user-1",
            "surface": "cli",
            "surface_user_id": "whatever",
            "display_name": "tester",
            "created_at": "2024-01-01T00:00:00Z",
            "memory_namespace": "ns-cli",
        },
    )


def test_chat_renders_reply_and_recalled_memories(cli_env, monkeypatch, capsys):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            return user_response()
        if request.url.path == "/chat/turn":
            seen["turn"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "turn_id": "turn-1",
                    "user_id": "user-1",
                    "memory_namespace": "ns-cli",
                    "reply": "You told me you like tea.",
                    "recalled": [
                        {
                            "blob_id": "blob-abcdef123456",
                            "text": "likes tea",
                            "distance": 0.1,
                            "salience": 0.83,
                            "importance": 0.5,
                            "origin_surface": "telegram",
                            "age_days": 2.0,
                        }
                    ],
                    "stored_facts": [],
                    "skipped_duplicates": 0,
                    "contradiction_count": 0,
                    "memory_degraded": False,
                },
            )
        raise AssertionError(f"unexpected request {request.url.path}")

    inputs = iter(["tester", "hello there", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    code = main(["chat"], transport=make_transport(handler))

    assert code == 0
    out = capsys.readouterr().out
    assert "You told me you like tea." in out
    assert "MEMORY" in out
    assert "likes tea" in out
    assert "salience=0.83" in out
    assert "telegram" in out
    assert seen["turn"]["text"] == "hello there"
    assert seen["turn"]["memory_enabled"] is True
    assert seen["turn"]["surface"] == "cli"


def test_chat_nomem_toggles_memory_enabled(cli_env, monkeypatch, capsys):
    flags = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            return user_response()
        if request.url.path == "/chat/turn":
            flags.append(json.loads(request.content)["memory_enabled"])
            return httpx.Response(
                200,
                json={
                    "turn_id": "turn-1",
                    "user_id": "user-1",
                    "memory_namespace": "ns-cli",
                    "reply": "ok",
                    "recalled": [],
                    "stored_facts": [],
                    "skipped_duplicates": 0,
                    "contradiction_count": 0,
                    "memory_degraded": False,
                },
            )
        raise AssertionError(f"unexpected request {request.url.path}")

    inputs = iter(["tester", "/nomem", "first", "/nomem", "second", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main(["chat"], transport=make_transport(handler)) == 0
    assert flags == [False, True]
    assert "no memories recalled" in capsys.readouterr().out


def test_connection_failure_names_base_url_and_exits_one(cli_env, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    code = main(["stats"], transport=make_transport(handler))

    assert code == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "RANTI_API_URL" in combined
    assert BASE in combined
    assert "Traceback" not in combined


def test_non_2xx_reports_api_code_and_status(cli_env, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            return user_response()
        return httpx.Response(404, json={"error": "not_found", "detail": "no such user"})

    code = main(["stats"], transport=make_transport(handler))

    assert code == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "[FAIL]" in combined
    assert "not_found" in combined
    assert "HTTP 404" in combined
    assert "Traceback" not in combined


def test_passport_import_sends_file_contents(cli_env, tmp_path, capsys):
    passport = {
        "format": "ranti.memory-passport.v1",
        "user": {"surface": "cli", "surface_user_id": "abc", "display_name": "tester"},
        "namespace": "ns-cli",
        "memories": [
            {"text": "likes tea", "importance": 0.5, "origin_surface": "cli"},
            {"text": "lives in Berlin", "importance": 0.6, "origin_surface": "cli"},
        ],
    }
    source = tmp_path / "passport.json"
    source.write_text(json.dumps(passport), encoding="utf-8")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/memories/passport/import"
        assert request.method == "POST"
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "user_id": "user-2",
                "namespace": "ns-cli",
                "imported": 3,
                "skipped": 1,
            },
        )

    code = main(["passport", "import", str(source)], transport=make_transport(handler))

    assert code == 0
    assert seen["body"] == passport
    out = capsys.readouterr().out
    assert "imported 3" in out
    assert "skipped 1" in out


def test_identity_is_persisted_and_reused_across_invocations(cli_env, capsys):
    registrations = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            registrations.append(json.loads(request.content))
            return user_response()
        if request.url.path == "/memories/user-1/stats":
            return httpx.Response(
                200,
                json={
                    "user_id": "user-1",
                    "display_name": "cli-user",
                    "surface": "cli",
                    "namespace": "ns-cli",
                    "active": 2,
                    "superseded": 0,
                    "contradicted": 0,
                    "open_contradictions": 0,
                    "turns": 1,
                    "relayer_memory_count": 2,
                    "relayer_storage_bytes": 128,
                    "relayer_degraded": False,
                },
            )
        raise AssertionError(f"unexpected request {request.url.path}")

    transport = make_transport(handler)
    assert main(["stats"], transport=transport) == 0
    first_file = json.loads(cli_env.read_text(encoding="utf-8"))
    assert main(["stats"], transport=transport) == 0

    assert len(registrations) == 2
    assert registrations[0]["surface_user_id"]
    assert registrations[0]["surface_user_id"] == registrations[1]["surface_user_id"]

    second_file = json.loads(cli_env.read_text(encoding="utf-8"))
    assert first_file == second_file
    assert cli_env.stat().st_mode & 0o777 == 0o600
    assert stat.S_IMODE(cli_env.stat().st_mode) == 0o600


def test_identity_file_has_surface_user_id_and_display_name(cli_env, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            return user_response()
        raise AssertionError(f"unexpected request {request.url.path}")

    inputs = iter(["", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main(["chat"], transport=make_transport(handler)) == 0

    data = json.loads(cli_env.read_text(encoding="utf-8"))
    assert data["surface"] == "cli"
    assert data["display_name"] == "cli-user"
    assert len(data["surface_user_id"]) >= 16


def test_unwritable_identity_path_reports_failure(cli_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RANTI_CLI_CONFIG", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call expected before identity is saved")

    code = main(["stats"], transport=make_transport(handler))

    assert code == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "identity file" in combined
    assert "Traceback" not in combined
