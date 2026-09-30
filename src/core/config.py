"""Configuration. One frozen object built from the environment at the composition root."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


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

    memwal_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 30.0
    telegram_timeout_seconds: float = 10.0

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
        source = env if env is not None else os.environ

        candidates: Sequence[LlmProviderConfig] = (
            LlmProviderConfig(
                name="groq",
                base_url=_get(source, "GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                api_key=_get(source, "GROQ_API_KEY"),
                model=_get(source, "GROQ_MODEL", "qwen3-32b"),
            ),
            LlmProviderConfig(
                name="gemini",
                base_url=_get(
                    source,
                    "GEMINI_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai/",
                ),
                api_key=_get(source, "GEMINI_API_KEY"),
                model=_get(source, "GEMINI_MODEL", "gemini-2.5-flash-lite"),
            ),
            LlmProviderConfig(
                name="ollama",
                base_url=_get(source, "OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                api_key="",
                model=_get(source, "OLLAMA_MODEL", "qwen2.5:1.5b"),
            ),
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
            llm_providers=tuple(candidate for candidate in candidates if candidate.configured),
        )
