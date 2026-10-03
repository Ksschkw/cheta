"""Persistence for the user entity. One entity, no cross-entity joins."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from core.database import Database
from models.entities.user_model import UserModel

PAIRING_REGISTRY_PATH = Path("artifacts/pairing_registry.json")


def _load_pairing_registry() -> dict[str, str]:
    if not PAIRING_REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(PAIRING_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_pairing_registry(registry: dict[str, str]) -> None:
    try:
        PAIRING_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        PAIRING_REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception:
        pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_model(row) -> UserModel:
    return UserModel(
        id=row["id"],
        surface=row["surface"],
        surface_user_id=row["surface_user_id"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        memory_handle=(
            row["memory_handle"] if "memory_handle" in row.keys() else None
        ),
        linked_at=(row["linked_at"] if "linked_at" in row.keys() else None),
    )


class UserCrud:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_by_id(self, user_id: str) -> UserModel | None:
        row = self._database.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return _row_to_model(row) if row else None

    def get_by_identity(self, surface: str, surface_user_id: str) -> UserModel | None:
        row = self._database.fetch_one(
            "SELECT * FROM users WHERE surface = ? AND surface_user_id = ?",
            (surface, surface_user_id),
        )
        return _row_to_model(row) if row else None

    def list(self, limit: int = 100, offset: int = 0) -> list[UserModel]:
        rows = self._database.fetch_all(
            "SELECT * FROM users ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [_row_to_model(row) for row in rows]

    def create(
        self, surface: str, surface_user_id: str, display_name: str
    ) -> UserModel:
        model = UserModel(
            id=str(uuid.uuid4()),
            surface=surface,
            surface_user_id=surface_user_id,
            display_name=display_name,
            created_at=_now(),
        )
        self._database.execute(
            "INSERT INTO users (id, surface, surface_user_id, display_name, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (model.id, model.surface, model.surface_user_id, model.display_name, model.created_at),
        )
        return model

    def get_or_create(
        self, surface: str, surface_user_id: str, display_name: str
    ) -> UserModel:
        """Persist-level upsert. The single rule that one surface identity is one user.

        Shared by every use case that needs an identity, so no two services have
        to import each other to reuse it.
        """
        existing = self.get_by_identity(surface, surface_user_id)
        if existing is None:
            user = self.create(surface, surface_user_id, display_name)
            if self._database.path != ":memory:":
                reg = _load_pairing_registry()
                key = f"{surface}:{surface_user_id}"
                if key in reg:
                    user = self.set_memory_handle(user.id, reg[key]) or user
            return user
        if existing.memory_handle is None and self._database.path != ":memory:":
            reg = _load_pairing_registry()
            key = f"{surface}:{surface_user_id}"
            if key in reg:
                existing = self.set_memory_handle(existing.id, reg[key]) or existing
        if existing.display_name != display_name:
            return self.update(existing.id, display_name) or existing
        return existing

    def set_memory_handle(self, user_id: str, handle: str | None) -> UserModel | None:
        """Bind or clear the shared handle. Validation lives in the entity.

        Joining a space records when it happened, so /sessions can say when each
        client was linked; leaving clears it again.
        """
        from models.entities.user_model import UserModel as _UserModel

        current = self.get_by_id(user_id)
        if current is None:
            return None
        # Reuse the entity invariant rather than duplicating the rules here.
        _UserModel(
            id=current.id,
            surface=current.surface,
            surface_user_id=current.surface_user_id,
            display_name=current.display_name,
            created_at=current.created_at,
            memory_handle=handle,
        )
        linked_at = _now() if handle is not None else None
        changed = self._database.execute(
            "UPDATE users SET memory_handle = ?, linked_at = ? WHERE id = ?",
            (handle, linked_at, user_id),
        )
        if changed and self._database.path != ":memory:":
            try:
                reg = _load_pairing_registry()
                key = f"{current.surface}:{current.surface_user_id}"
                if handle is not None:
                    reg[key] = handle
                else:
                    reg.pop(key, None)
                _save_pairing_registry(reg)
            except Exception:
                pass
        return self.get_by_id(user_id) if changed else None

    def list_by_memory_handle(self, handle: str) -> list[UserModel]:
        """Every identity currently sharing one handle. One entity, no joins."""
        rows = self._database.fetch_all(
            "SELECT * FROM users WHERE memory_handle = ?"
            " ORDER BY linked_at ASC, created_at ASC, surface ASC",
            (handle,),
        )
        return [_row_to_model(row) for row in rows]

    def update(self, user_id: str, display_name: str) -> UserModel | None:
        if not display_name.strip():
            raise ValueError("display_name must not be blank")
        changed = self._database.execute(
            "UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id)
        )
        return self.get_by_id(user_id) if changed else None

    def delete(self, user_id: str) -> bool:
        return bool(self._database.execute("DELETE FROM users WHERE id = ?", (user_id,)))
