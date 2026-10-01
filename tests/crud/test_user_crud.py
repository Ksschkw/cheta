"""Persistence contract for the user entity."""

from __future__ import annotations

import pytest

from core.database import Database
from crud.user_crud import UserCrud


@pytest.fixture
def crud() -> UserCrud:
    database = Database(":memory:")
    database.migrate()
    return UserCrud(database)


def test_create_then_read_back_by_identity(crud: UserCrud) -> None:
    created = crud.create("telegram", "42", "Ada")
    fetched = crud.get_by_identity("telegram", "42")

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.display_name == "Ada"
    assert crud.get_by_id(created.id).surface_user_id == "42"  # type: ignore[union-attr]


def test_lookup_of_unknown_identity_returns_none(crud: UserCrud) -> None:
    assert crud.get_by_identity("telegram", "does-not-exist") is None
    assert crud.get_by_id("nope") is None


def test_duplicate_identity_is_rejected_by_storage(crud: UserCrud) -> None:
    crud.create("cli", "kooka", "K")
    with pytest.raises(Exception) as error:
        crud.create("cli", "kooka", "K again")
    assert "UNIQUE" in str(error.value).upper()


def test_list_is_stable_and_ordered(crud: UserCrud) -> None:
    first = crud.create("telegram", "1", "One")
    second = crud.create("telegram", "2", "Two")

    listed = crud.list()
    assert [user.id for user in listed] == [first.id, second.id]


def test_update_changes_name_and_reports_missing_row(crud: UserCrud) -> None:
    created = crud.create("web", "widget-1", "Guest")

    updated = crud.update(created.id, "Grace")
    assert updated is not None and updated.display_name == "Grace"
    assert crud.update("missing-id", "Nobody") is None


def test_update_rejects_blank_name(crud: UserCrud) -> None:
    created = crud.create("web", "widget-2", "Guest")
    with pytest.raises(ValueError):
        crud.update(created.id, "   ")


def test_delete_is_reported_once(crud: UserCrud) -> None:
    created = crud.create("telegram", "9", "Nine")

    assert crud.delete(created.id) is True
    assert crud.delete(created.id) is False
    assert crud.get_by_id(created.id) is None


def test_the_additive_migration_is_idempotent(crud: UserCrud) -> None:
    """Existing databases hold real rows, so the column must add safely twice."""
    from core.database import Database

    database = Database(":memory:")
    database.migrate()
    database.migrate()

    users = UserCrud(database)
    created = users.create("telegram", "1", "Ada")

    assert created.memory_handle is None
    assert users.set_memory_handle(created.id, "ada-shared").memory_handle == "ada-shared"
    assert users.set_memory_handle(created.id, None).memory_handle is None


def test_set_memory_handle_rejects_an_invalid_handle(crud: UserCrud) -> None:
    created = crud.create("telegram", "2", "Ada")

    with pytest.raises(ValueError):
        crud.set_memory_handle(created.id, "Not Valid")
    assert crud.get_by_id(created.id).memory_handle is None  # type: ignore[union-attr]
