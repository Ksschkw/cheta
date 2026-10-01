"""The pure pairing-code rules: alphabet, normalisation and hashing."""

from __future__ import annotations

from models.entities.pairing_code_model import (
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    PairingCodeModel,
    generate_pairing_code,
    looks_like_pairing_code,
    normalize_pairing_code,
    pairing_code_hash,
)

# One character from every ambiguous pair the alphabet must not contain.
CONFUSABLE = set("01OILZSB")


def test_generated_codes_avoid_every_confusable_character() -> None:
    codes = {generate_pairing_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert len(code) == PAIRING_CODE_LENGTH
        assert set(code) <= set(PAIRING_CODE_ALPHABET)
    assert CONFUSABLE.isdisjoint(PAIRING_CODE_ALPHABET)


def test_normalisation_ignores_case_spacing_and_hyphens() -> None:
    assert normalize_pairing_code("  abcd-2345 ") == "ABCD2345"
    assert normalize_pairing_code("ABCD 2345") == normalize_pairing_code("abcd2345")


def test_only_a_code_shaped_argument_looks_like_a_code() -> None:
    assert looks_like_pairing_code(generate_pairing_code()) is True
    assert looks_like_pairing_code("acde-2345") is True
    assert looks_like_pairing_code("of shoes") is False
    assert looks_like_pairing_code("AB") is False


def test_the_hash_hides_the_code_and_depends_on_the_pepper() -> None:
    code = generate_pairing_code()

    digest = pairing_code_hash("pepper-one", code)
    assert code not in digest
    assert digest == pairing_code_hash("pepper-one", code)
    assert digest != pairing_code_hash("pepper-two", code)


def test_expiry_is_decided_by_the_stored_timestamp() -> None:
    model = PairingCodeModel(
        id="p1",
        user_id="u1",
        surface="telegram",
        surface_user_id="42",
        code_hash="deadbeef",
        created_at="2025-01-01T00:00:00+00:00",
        expires_at="2025-01-01T00:10:00+00:00",
    )

    assert model.is_expired("2025-01-01T00:09:59+00:00") is False
    assert model.is_expired("2025-01-01T00:10:00+00:00") is True
    assert model.redeemed is False
