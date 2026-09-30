"""User entity. Imports nothing from this project."""

from __future__ import annotations

from dataclasses import dataclass

VALID_SURFACES = ("telegram", "cli", "web")


@dataclass(frozen=True)
class UserModel:
    """A person as seen by one surface.

    ``surface_user_id`` is the identity that surface gives us. The same person on
    Telegram and in the CLI is two users with two memory namespaces; that is the
    isolation boundary Walrus Memory enforces, and crossing it is a deliberate
    product feature (the Memory Passport), never an accident.
    """

    id: str
    surface: str
    surface_user_id: str
    display_name: str
    created_at: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("user id is required")
        if self.surface not in VALID_SURFACES:
            raise ValueError(f"surface must be one of {VALID_SURFACES}, got {self.surface!r}")
        if not self.surface_user_id:
            raise ValueError("surface_user_id is required")
        if not self.display_name.strip():
            raise ValueError("display_name must not be blank")

    @property
    def memory_key(self) -> str:
        """Stable, filesystem-safe key used to derive the memory namespace."""
        return f"{self.surface}-{self.surface_user_id}"
