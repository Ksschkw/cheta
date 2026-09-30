"""User transport DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserCreateSchema(BaseModel):
    surface: str = Field(min_length=1, max_length=32)
    surface_user_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)


class UserSchema(BaseModel):
    id: str
    surface: str
    surface_user_id: str
    display_name: str
    created_at: str
    memory_namespace: str
