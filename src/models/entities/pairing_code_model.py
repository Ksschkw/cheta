"""Pairing code entity and its pure rules. Imports nothing from this project.

A pairing code is the short string one client shows and a second client
redeems. Every confusing character is removed from the alphabet because the
code is read aloud or retyped by hand: one character is dropped from each
visually ambiguous pair (0/O, 1/I/L, 2/Z, 5/S, 8/B). The stored form is a
salted hash, never the code itself.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

# One character from every confusable pair is dropped:
#   0 and O, 1, I and L, 2 and Z, 5 and S, 8 and B.
PAIRING_CODE_ALPHABET = "23456789ACDEFGHJKMNPQRTUVWXY"
PAIRING_CODE_LENGTH = 8
# The shortest and longest argument that is treated as a code worth trying.
PAIRING_CODE_MIN_LENGTH = 6
PAIRING_CODE_MAX_LENGTH = 10


def generate_pairing_code(length: int = PAIRING_CODE_LENGTH) -> str:
    """A random code drawn from the unambiguous alphabet."""
    if length < PAIRING_CODE_MIN_LENGTH:
        raise ValueError(
            f"pairing code must be at least {PAIRING_CODE_MIN_LENGTH} characters"
        )
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(length))


def normalize_pairing_code(raw: str) -> str:
    """Upper-case and strip the separators people add when retyping.

    Whitespace and hyphens are removed, so "abcd-2345" and " ABCD 2345 " both
    normalise to the same code. Letters that are not in the alphabet are kept:
    a typed O or I stays in the string, and the hash simply will not match,
    which is the honest outcome for a typo.
    """
    return "".join(
        character
        for character in raw.upper()
        if not character.isspace() and character != "-"
    )


def looks_like_pairing_code(raw: str) -> bool:
    """True when a bare argument is plausibly a code rather than prose.

    Used to keep the no-slash form ``pair <code>`` from swallowing an ordinary
    message like "pair of shoes": that argument contains letters outside the
    alphabet, so it is not treated as a command.
    """
    normalized = normalize_pairing_code(raw)
    if not PAIRING_CODE_MIN_LENGTH <= len(normalized) <= PAIRING_CODE_MAX_LENGTH:
        return False
    return all(character in PAIRING_CODE_ALPHABET for character in normalized)


def pairing_code_hash(pepper: str, code: str) -> str:
    """Stable hash of a normalised code, domain separated by a pepper.

    The row stores this digest, never the code. Lookup is by digest, so the
    pepper must be the same at issue time and at redemption time.
    """
    return hashlib.sha256(f"{pepper}:{code}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PairingCodeModel:
    """One issued code, its lifecycle, and who issued it.

    ``user_id`` is the originating identity, so redeeming can only ever attach
    the new client to that person's memory space. ``surface`` and
    ``surface_user_id`` are kept so the originating client can be told when
    someone redeems.
    """

    id: str
    user_id: str
    surface: str
    surface_user_id: str
    code_hash: str
    created_at: str
    expires_at: str
    redeemed_at: str | None = None
    redeemed_by_user_id: str | None = None
    invalidated_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("pairing code id is required")
        if not self.user_id:
            raise ValueError("pairing code user_id is required")
        if not self.surface:
            raise ValueError("pairing code surface is required")
        if not self.surface_user_id:
            raise ValueError("pairing code surface_user_id is required")
        if not self.code_hash:
            raise ValueError("pairing code hash is required")
        if not self.created_at:
            raise ValueError("pairing code created_at is required")
        if not self.expires_at:
            raise ValueError("pairing code expires_at is required")

    @property
    def redeemed(self) -> bool:
        return self.redeemed_at is not None

    @property
    def invalidated(self) -> bool:
        return self.invalidated_at is not None

    def is_expired(self, now_iso: str) -> bool:
        """True when the moment is at or past the expiry.

        ISO-8601 UTC timestamps compare correctly as strings, and both sides
        are written by this project with the same shape, so no parsing is
        needed here.
        """
        return now_iso >= self.expires_at
