"""Telegram rejects over-length profile fields, so the limits are asserted here.

These are not arbitrary numbers: setMyShortDescription caps at 120 characters and
setMyDescription at 512, and a command description must be 3 to 256 characters.
Exceeding any of them fails at call time, which is a poor place to find out.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_telegram_profile.py"


def load_module():
    spec = importlib.util.spec_from_file_location("telegram_profile_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_short_description_fits_the_telegram_limit() -> None:
    module = load_module()

    assert 0 < len(module.SHORT_DESCRIPTION) <= 120


def test_the_description_fits_the_telegram_limit() -> None:
    module = load_module()

    assert 0 < len(module.DESCRIPTION) <= 512


def test_every_command_is_within_telegrams_constraints() -> None:
    module = load_module()

    assert module.COMMANDS, "a command menu with no commands is pointless"
    for name, description in module.COMMANDS:
        assert 1 <= len(name) <= 32
        assert 3 <= len(description) <= 256
        assert name == name.lower()
        assert not name.startswith("/"), "the slash is added by the API payload"


def test_the_profile_is_ascii_only() -> None:
    module = load_module()

    for value in (module.SHORT_DESCRIPTION, module.DESCRIPTION):
        assert value.isascii()
    for name, description in module.COMMANDS:
        assert name.isascii() and description.isascii()
