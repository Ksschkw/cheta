"""Architecture and house-rule checks. These fail the default test command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from importlinter.cli import lint_imports

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"

FORBIDDEN_FILENAMES = {"utils.py", "helpers.py", "common.py", "helper.py", "util.py"}


@pytest.fixture(scope="module")
def lint_exit_code() -> int:
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    return lint_imports(config_filename=str(REPO_ROOT / "pyproject.toml"), no_cache=True)


def test_import_contracts_hold(lint_exit_code: int) -> None:
    assert lint_exit_code == 0, "import-linter reported a broken dependency direction"


def test_no_generic_dumping_ground_modules() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if path.name in FORBIDDEN_FILENAMES
    ]
    assert offenders == [], f"forbidden catch-all modules present: {offenders}"


def test_source_tree_contains_no_emoji_or_decorative_unicode() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(ord(character) > 127 for character in line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    assert offenders == [], f"non-ASCII characters found in source: {offenders}"
