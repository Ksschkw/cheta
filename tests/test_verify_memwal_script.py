"""The credential verifier's surroundings: parsing and precedence.

The round trip itself needs real credentials and lives in
tests/integration/test_live_walrus_memory.py. What is tested here is the part
that decides which values get used, because silently reading the wrong value is
how a credential bug hides.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_memwal.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("verify_memwal_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return load_script_module()


def test_env_file_parsing_skips_comments_and_blank_lines(script, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "MEMWAL_ACCOUNT_ID=0xabc123\n"
        "MEMWAL_SERVER_URL=https://relayer.memory.walrus.xyz\n"
        "  MEMWAL_NAMESPACE_PREFIX = ranti  \n",
        encoding="utf-8",
    )

    parsed = script.load_env_file(env_file)

    assert parsed["MEMWAL_ACCOUNT_ID"] == "0xabc123"
    assert parsed["MEMWAL_SERVER_URL"] == "https://relayer.memory.walrus.xyz"
    assert parsed["MEMWAL_NAMESPACE_PREFIX"] == "ranti"
    assert "# a comment" not in parsed


def test_a_value_containing_equals_is_kept_intact(script, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_WEBHOOK_SECRET=a=b=c\n", encoding="utf-8")

    assert script.load_env_file(env_file)["TELEGRAM_WEBHOOK_SECRET"] == "a=b=c"


def test_a_missing_env_file_is_not_an_error(script, tmp_path: Path) -> None:
    assert script.load_env_file(tmp_path / "does-not-exist") == {}


def test_real_environment_variables_beat_the_env_file(script, monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MEMWAL_ACCOUNT_ID=from-file\n", encoding="utf-8")
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("MEMWAL_ACCOUNT_ID", "from-process")

    merged = script.merged_environment()

    assert merged["MEMWAL_ACCOUNT_ID"] == "from-process"


def test_the_verification_namespace_is_isolated_from_user_namespaces(script) -> None:
    """The verifier must never write into a real user's memory space."""
    assert script.VERIFY_NAMESPACE == "ranti.verify"
    assert ".user." not in script.VERIFY_NAMESPACE
