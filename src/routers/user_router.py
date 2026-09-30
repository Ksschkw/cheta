"""User routes. Parse, call one service, shape the response."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from core.container import Container, get_container
from schemas.user_schema import UserCreateSchema, UserSchema


def build_router() -> APIRouter:
    router = APIRouter(prefix="/users", tags=["users"])

    @router.post("", response_model=UserSchema, status_code=201)
    def register(
        payload: UserCreateSchema, container: Container = Depends(get_container)
    ) -> UserSchema:
        return container.user_service.register_or_get(
            surface=payload.surface,
            surface_user_id=payload.surface_user_id,
            display_name=payload.display_name,
        )

    @router.get("", response_model=list[UserSchema])
    def list_users(
        limit: int = 100, offset: int = 0, container: Container = Depends(get_container)
    ) -> list[UserSchema]:
        return container.user_service.list(limit=limit, offset=offset)

    @router.get("/{user_id}", response_model=UserSchema)
    def get_user(user_id: str, container: Container = Depends(get_container)) -> UserSchema:
        return container.user_service.get(user_id)

    @router.patch("/{user_id}", response_model=UserSchema)
    def rename_user(
        user_id: str, payload: UserCreateSchema, container: Container = Depends(get_container)
    ) -> UserSchema:
        return container.user_service.rename(user_id, payload.display_name)

    @router.delete("/{user_id}", status_code=204)
    def delete_user(user_id: str, container: Container = Depends(get_container)) -> Response:
        container.user_service.delete(user_id)
        return Response(status_code=204)

    return router
