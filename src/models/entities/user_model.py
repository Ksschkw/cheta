"""User entity. Imports nothing from this project."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Every surface that can hold a conversation. The Chrome extension was
# shipped before being added here, so every one of its turns returned 500.
VALID_SURFACES = ("telegram", "cli", "web", "extension")


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
    # A person-chosen key shared across surfaces. When set, every client using
    # this handle resolves to one memory space. When unset, the per-surface key
    # is used, which is exactly what every existing user has today, so nothing
    # stored before this existed is orphaned.
    memory_handle: str | None = None
    # When this identity last joined its shared space. /sessions shows it, and
    # it falls back to created_at for identities that joined before the column
    # existed.
    linked_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("user id is required")
        if self.surface not in VALID_SURFACES:
            raise ValueError(f"surface must be one of {VALID_SURFACES}, got {self.surface!r}")
        if not self.surface_user_id:
            raise ValueError("surface_user_id is required")
        if not self.display_name.strip():
            raise ValueError("display_name must not be blank")
        if self.memory_handle is not None:
            handle = self.memory_handle
            if not 3 <= len(handle) <= 64:
                raise ValueError("memory handle must be between 3 and 64 characters")
            if not re.fullmatch(r"[a-z0-9_-]+", handle):
                raise ValueError(
                    "memory handle may contain only lowercase letters, digits, hyphen "
                    "and underscore"
                )

    @property
    def memory_key(self) -> str:
        """Key used to derive the memory namespace.

        The shared handle wins when set, which is what makes the same person the
        same memory space on every surface.
        """
        if self.memory_handle:
            return f"shared-{self.memory_handle}"
        return f"{self.surface}-{self.surface_user_id}"
