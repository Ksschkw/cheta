"""Configuration. One frozen object built from the environment at the composition root."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# src/core/config.py -> the repository root.
DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines.

    Deliberately forgiving about whitespace around the value and about matching
    surrounding quotes, because both are common in hand-edited .env files and
    both silently produce an empty variable when a shell sources the file.
    A '#' inside a value is preserved: only whole-line comments are treated as
    comments, so a secret containing '#' is not truncated.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_environment(path: Path | None = None) -> Mapping[str, str]:
    """Read ``.env`` and overlay the real process environment on top of it.

    The app loads this itself rather than relying on the caller to source the
    file. Without that, the documented command ``uvicorn main:app`` would start
    with no credentials and quietly fall back to the offline mock, which looks
    exactly like a broken integration.
    """
    values: dict[str, str] = {}
    candidate = path if path is not None else DOTENV_PATH
    if candidate.is_file():
        try:
            values.update(_parse_dotenv(candidate.read_text(encoding="utf-8")))
        except OSError:
            # An unreadable .env is not fatal: the process environment may still
            # carry everything, and the offline fallback keeps the app runnable.
            pass
    values.update(os.environ)
    return values


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class LlmProviderConfig:
    """One OpenAI-compatible provider in the failover chain."""

    name: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        # A local Ollama runs without an API key; hosted providers do not.
        return bool(self.base_url and self.model and (self.api_key or self.name == "ollama"))


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    database_path: str = "artifacts/ranti.db"

    memwal_private_key: str = ""
    memwal_account_id: str = ""
    memwal_server_url: str = "https://relayer.memory.walrus.xyz"
    memwal_namespace_prefix: str = "ranti"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    public_base_url: str = "http://127.0.0.1:8000"

    llm_providers: tuple[LlmProviderConfig, ...] = field(default_factory=tuple)

    # Hold a free host awake. Enabled by default only for an https deployment,
    # because a always-on free instance consumes essentially the whole monthly
    # instance-hour allowance.
    keepalive_enabled: bool = True

    memwal_timeout_seconds: float = 90.0
    llm_timeout_seconds: float = 30.0
    telegram_timeout_seconds: float = 10.0

    @property
    def keepalive_target(self) -> str | None:
        """The public https URL to ping, or None when there is nothing to keep awake."""
        if not self.keepalive_enabled:
            return None
        if not self.public_base_url.startswith("https://"):
            return None
        return self.public_base_url

    @property
    def memwal_configured(self) -> bool:
        return bool(self.memwal_private_key and self.memwal_account_id)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token)

    def memory_namespace(self, user_key: str) -> str:
        """Namespace per user. Flat, stable, within the 255-byte server limit."""
        raw = f"{self.memwal_namespace_prefix}.user.{user_key}"
        encoded = raw.encode("utf-8")
        if len(encoded) > 255:
            raise ValueError("memory namespace exceeds the 255-byte Walrus Memory limit")
        if "\x00" in raw:
            raise ValueError("memory namespace must not contain a NUL byte")
        return raw

    def index_namespace(self, user_key: str) -> str:
        """Companion namespace holding the portable consolidation index."""
        return f"{self.memory_namespace(user_key)}.idx"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        # An explicit mapping is used verbatim so tests stay hermetic. The
        # default reads .env and then the process environment.
        source = env if env is not None else load_environment()

        # Ollama needs no API key, so it cannot be detected by looking for one.
        # It is opt-in instead: otherwise a fresh clone would select a local
        # daemon that is probably not running and every turn would fail, instead
        # of falling through to the deterministic offline model.
        ollama_enabled = _get(source, "OLLAMA_ENABLED", "0").lower() in ("1", "true", "yes", "on")

        candidates: list[LlmProviderConfig] = [
            LlmProviderConfig(
                name="groq",
                base_url=_get(source, "GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                api_key=_get(source, "GROQ_API_KEY"),
                model=_get(source, "GROQ_MODEL", "qwen/qwen3.8-27b"),
            ),
            LlmProviderConfig(
                name="gemini",
                base_url=_get(
                    source,
                    "GEMINI_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai/",
                ),
                api_key=_get(source, "GEMINI_API_KEY"),
                model=_get(source, "GEMINI_MODEL", "gemini-3.1-flash-lite"),
            ),
        ]
        if ollama_enabled:
            candidates.append(
                LlmProviderConfig(
                    name="ollama",
                    base_url=_get(source, "OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                    api_key="",
                    model=_get(source, "OLLAMA_MODEL", "qwen2.5:1.5b"),
                )
            )

        return cls(
            environment=_get(source, "RANTI_ENVIRONMENT", "development"),
            database_path=_get(source, "RANTI_DATABASE_PATH", "artifacts/ranti.db"),
            memwal_private_key=_get(source, "MEMWAL_PRIVATE_KEY"),
            memwal_account_id=_get(source, "MEMWAL_ACCOUNT_ID"),
            memwal_server_url=_get(
                source, "MEMWAL_SERVER_URL", "https://relayer.memory.walrus.xyz"
            ),
            memwal_namespace_prefix=_get(source, "MEMWAL_NAMESPACE_PREFIX", "ranti"),
            telegram_bot_token=_get(source, "TELEGRAM_BOT_TOKEN"),
            telegram_webhook_secret=_get(source, "TELEGRAM_WEBHOOK_SECRET"),
            public_base_url=_get(source, "PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
            memwal_timeout_seconds=_get_float(source, "MEMWAL_TIMEOUT_SECONDS", 90.0),
            keepalive_enabled=_get(source, "RANTI_KEEPALIVE", "1").lower()
            not in ("0", "false", "no", "off"),
            llm_providers=tuple(candidate for candidate in candidates if candidate.configured),
        )
