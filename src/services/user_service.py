"""User use cases."""

from __future__ import annotations

from core.config import Settings
from core.errors import ConflictError, NotFoundError, ValidationError
from crud.user_crud import UserCrud
from models.entities.user_model import UserModel
from schemas.user_schema import UserSchema


class UserService:
    """Registration and lookup. Identity is per surface by design."""

    def __init__(self, users: UserCrud, settings: Settings) -> None:
        self._users = users
        self._settings = settings

    def _to_schema(self, user: UserModel) -> UserSchema:
        return UserSchema(
            id=user.id,
            surface=user.surface,
            surface_user_id=user.surface_user_id,
            display_name=user.display_name,
            created_at=user.created_at,
            memory_namespace=self._settings.memory_namespace(user.memory_key),
        )

    def register_or_get(
        self, surface: str, surface_user_id: str, display_name: str
    ) -> UserSchema:
        """Idempotent registration. A repeat visit never creates a second identity."""
        return self._to_schema(
            self._users.get_or_create(surface, surface_user_id, display_name)
        )

    def get(self, user_id: str) -> UserSchema:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        return self._to_schema(user)

    def list(self, limit: int = 100, offset: int = 0) -> list[UserSchema]:
        return [self._to_schema(user) for user in self._users.list(limit, offset)]

    def rename(self, user_id: str, display_name: str) -> UserSchema:
        if not display_name.strip():
            raise ValidationError("display_name must not be blank")
        updated = self._users.update(user_id, display_name)
        if updated is None:
            raise NotFoundError(f"user {user_id} does not exist")
        return self._to_schema(updated)

    def delete(self, user_id: str) -> None:
        if not self._users.delete(user_id):
            raise ConflictError(f"user {user_id} was already removed")
