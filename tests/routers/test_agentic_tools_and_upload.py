"""Tests for the agentic tools catalog, document upload, and offline tool execution."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from core.config import Settings
from main import create_app


def test_list_tools_endpoint_returns_all_registered_tools() -> None:
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="test")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/chat/tools")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        tool_names = [tool["name"] for tool in data]
        assert "calculate" in tool_names
        assert "weather" in tool_names
        assert "wikipedia" in tool_names
        assert "web_search" in tool_names
        assert "crawl" in tool_names
        assert "calendar_event" in tool_names
        assert "reminder_set" in tool_names
        assert "reminder_list" in tool_names
        assert "memory_recall" in tool_names
        assert "memory_remember" in tool_names
        assert "memory_forget" in tool_names
        assert "document_question" in tool_names
        assert len(tool_names) >= 13


def test_tools_command_returns_capabilities_listing() -> None:
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="test")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        turn = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "tools-user",
                "display_name": "Explorer",
                "text": "/tools",
            },
        ).json()
        assert "Cheta Agentic Capabilities" in turn["reply"]
        assert "calculate" in turn["reply"]
        assert "weather" in turn["reply"]


def test_offline_deterministic_tool_calling_for_calculation() -> None:
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="test")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        turn = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "calc-user",
                "display_name": "MathTester",
                "text": "Calculate 45 * 20",
            },
        ).json()
        assert "900" in turn["reply"]
        assert "calculate" in turn["tool_activity"]


def test_upload_document_endpoint_extracts_and_runs_turn() -> None:
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="test")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        doc_content = b"Walrus Protocol is a decentralized blob storage protocol built for autonomous agents."
        b64_content = base64.b64encode(doc_content).decode("ascii")

        turn = client.post(
            "/chat/upload",
            json={
                "surface": "web",
                "surface_user_id": "upload-user",
                "display_name": "Uploader",
                "filename": "walrus_notes.txt",
                "content_base64": b64_content,
                "question": "What is this document about?",
            },
        ).json()
        assert turn["turn_id"] or turn["reply"]
        assert len(turn["reply"]) > 0
