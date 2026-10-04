"""How configuration is read, especially from a hand-edited .env file.

Two real incidents drive these tests. A key written as ``GROQ_API_KEY= gsk_...``
with a space after the equals sign is silently dropped when a shell sources the
file, because the shell reads it as an empty assignment followed by a command.
And because the app originally relied on the caller to source .env, the
documented ``uvicorn main:app`` command started with no credentials at all and
quietly used the offline mock. Both are tested here.
"""

from __future__ import annotations

from pathlib import Path

from core.config import Settings, _parse_dotenv, load_environment
from models.entities.memory_phrasing_model import is_person_fact


def write_env(tmp_path: Path, text: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return env_file


def test_a_space_after_the_equals_sign_is_tolerated(tmp_path: Path) -> None:
    """The exact bug that stopped the Groq key from reaching the app."""
    env_file = write_env(tmp_path, "GROQ_API_KEY= gsk_examplekey\nGROQ_MODEL=qwen3-32b\n")

    parsed = _parse_dotenv(env_file.read_text(encoding="utf-8"))

    assert parsed["GROQ_API_KEY"] == "gsk_examplekey"
    assert parsed["GROQ_MODEL"] == "qwen3-32b"


def test_surrounding_quotes_are_stripped(tmp_path: Path) -> None:
    env_file = write_env(
        tmp_path,
        'A="quoted value"\nB=\'single quoted\'\nC=unquoted\n',
    )

    parsed = _parse_dotenv(env_file.read_text(encoding="utf-8"))

    assert parsed == {"A": "quoted value", "B": "single quoted", "C": "unquoted"}


def test_a_hash_inside_a_value_is_preserved(tmp_path: Path) -> None:
    env_file = write_env(tmp_path, "SECRET=abc#def\n# a whole line comment\n")

    parsed = _parse_dotenv(env_file.read_text(encoding="utf-8"))

    assert parsed == {"SECRET": "abc#def"}


def test_blank_lines_comments_and_junk_are_skipped(tmp_path: Path) -> None:
    env_file = write_env(tmp_path, "\n# comment\nnot a pair\nKEY=value\n")

    assert _parse_dotenv(env_file.read_text(encoding="utf-8")) == {"KEY": "value"}


def test_the_process_environment_wins_over_the_file(monkeypatch, tmp_path: Path) -> None:
    env_file = write_env(tmp_path, "MEMWAL_ACCOUNT_ID=from-file\n")
    monkeypatch.setenv("MEMWAL_ACCOUNT_ID", "from-process")

    merged = load_environment(env_file)

    assert merged["MEMWAL_ACCOUNT_ID"] == "from-process"


def test_a_missing_env_file_still_yields_the_process_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMWAL_NAMESPACE_PREFIX", "from-process")

    merged = load_environment(tmp_path / "absent")

    assert merged["MEMWAL_NAMESPACE_PREFIX"] == "from-process"


def test_an_explicit_mapping_is_used_verbatim_so_tests_stay_hermetic() -> None:
    """Passing a mapping must NOT also read the real .env file."""
    settings = Settings.from_env({})

    assert settings.memwal_configured is False
    assert settings.llm_providers == ()
    assert settings.telegram_configured is False


def test_the_repository_dotenv_path_points_at_the_repo_root() -> None:
    from core.config import DOTENV_PATH

    assert DOTENV_PATH.name == ".env"
    assert (DOTENV_PATH.parent / "pyproject.toml").is_file()


def test_the_persona_is_configurable_without_touching_the_namespace() -> None:
    """Renaming the bot must never move the memory namespace."""
    default = Settings.from_env({})
    renamed = Settings.from_env({"BOT_NAME": "Something Else"})

    assert default.bot_name == "Cheta"
    assert renamed.bot_name == "Something Else"
    assert renamed.memory_namespace("telegram-1") == default.memory_namespace("telegram-1")
    assert default.memory_namespace("telegram-1").startswith("ranti.user.")


def test_memory_receipts_are_off_unless_explicitly_enabled() -> None:
    assert Settings.from_env({}).memory_receipts is False
    assert Settings.from_env({"RANTI_MEMORY_RECEIPTS": "1"}).memory_receipts is True


def test_the_resume_gap_defaults_to_six_hours_and_is_configurable() -> None:
    assert Settings.from_env({}).resume_after_hours == 6.0
    assert Settings.from_env({"RANTI_RESUME_AFTER_HOURS": "0.5"}).resume_after_hours == 0.5
    # A malformed value must not crash startup; it falls back to the default.
    assert Settings.from_env({"RANTI_RESUME_AFTER_HOURS": "soon"}).resume_after_hours == 6.0


def test_retired_assistant_names_are_trimmed_deduplicated_and_empty_disables() -> None:
    settings = Settings.from_env({"RANTI_RETIRED_NAMES": " Ranti , Cheta,Ranti,, "})

    assert settings.retired_assistant_names == ("Ranti", "Cheta")

    disabled = Settings.from_env({})
    assert disabled.retired_assistant_names == ()
    # An empty list is what disables the filter: the name no longer implies the
    # assistant, so the record stays a fact about the person.
    assert (
        is_person_fact(
            "Ranti is a memory-first assistant.", disabled.retired_assistant_names
        )
        is True
    )
