"""User use cases. Identity, isolation and the failure paths."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.database import Database
from core.errors import ConflictError, NotFoundError, ValidationError
from crud.user_crud import UserCrud
from services.user_service import UserService


@pytest.fixture
def service() -> UserService:
    database = Database(":memory:")
    database.migrate()
    return UserService(
        users=UserCrud(database),
        settings=Settings(database_path=":memory:", memwal_namespace_prefix="ranti"),
    )


def test_register_is_idempotent_for_one_identity(service: UserService) -> None:
    first = service.register_or_get("telegram", "555", "Ada")
    second = service.register_or_get("telegram", "555", "Ada Lovelace")

    assert second.id == first.id
    assert second.display_name == "Ada Lovelace"
    assert len(service.list()) == 1


def test_the_same_person_on_two_surfaces_gets_two_isolated_namespaces(
    service: UserService,
) -> None:
    from_telegram = service.register_or_get("telegram", "555", "Ada")
    from_cli = service.register_or_get("cli", "555", "Ada")

    assert from_telegram.id != from_cli.id
    assert from_telegram.memory_namespace != from_cli.memory_namespace
    assert from_telegram.memory_namespace.endswith("telegram-555")
    assert from_cli.memory_namespace.endswith("cli-555")


def test_get_of_unknown_user_raises_not_found(service: UserService) -> None:
    with pytest.raises(NotFoundError):
        service.get("missing-user")


def test_rename_rejects_blank_and_reports_missing(service: UserService) -> None:
    created = service.register_or_get("web", "widget-7", "Guest")

    with pytest.raises(ValidationError):
        service.rename(created.id, "  ")

    with pytest.raises(NotFoundError):
        service.rename("missing-user", "Nobody")

    renamed = service.rename(created.id, "Grace")
    assert renamed.display_name == "Grace"


def test_delete_twice_is_a_conflict_not_a_silent_success(service: UserService) -> None:
    created = service.register_or_get("web", "widget-8", "Guest")
    service.delete(created.id)

    with pytest.raises(ConflictError):
        service.delete(created.id)


def test_namespace_stays_within_the_server_byte_limit(service: UserService) -> None:
    created = service.register_or_get("telegram", "x" * 128, "Long")

    assert len(created.memory_namespace.encode("utf-8")) <= 255
    assert created.memory_namespace.startswith("ranti.user.telegram-")
