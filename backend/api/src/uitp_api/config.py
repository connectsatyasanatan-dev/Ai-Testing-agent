"""
uitp_api.config
===============
Application configuration via Pydantic-Settings.

All settings come from environment variables (UITP_ prefix) or a .env file.
Missing required settings raise a clear ValidationError at startup — not
silently at request time (P0-03 acceptance criterion).

SECURITY:
  - SecretStr fields never appear in repr() or logs.
  - No secret value is stored in DB, prompts, logs, or artifacts.
  - .env must never be committed (.gitignore covers it).
"""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LogFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class Settings(BaseSettings):
    """All configuration for the uitp-api application."""

    model_config = SettingsConfigDict(
        env_prefix="UITP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT

    secret_key: SecretStr = Field(
        ...,
        description=(
            "Secret key for session cookies (min 32 chars). "
            "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
        ),
    )
    base_url: str = "http://localhost:8000"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: SecretStr = Field(
        ...,
        description="SQLAlchemy async DSN, e.g. postgresql+asyncpg://user:pass@host/db",
    )

    # ── Artifact storage (Milestone 1: local filesystem) ─────────────────────
    artifact_storage_path: str = "./artifacts"
    artifact_retention_days: int = Field(default=30, ge=1)
    artifact_max_size_bytes: int = Field(default=10 * 1024 * 1024, ge=1)

    # ── Queue (Milestone 2 — not required for Milestone 1) ───────────────────
    redis_url: str | None = None

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_allowed_origins: list[str] = ["http://localhost:5173"]

    # ── Rate limiting ────────────────────────────────────────────────────────
    rate_limit_login: int = Field(default=10, ge=1)
    rate_limit_run_create: int = Field(default=30, ge=1)
    rate_limit_ai_generate: int = Field(default=10, ge=1)

    # ── AI provider (optional) ───────────────────────────────────────────────
    ai_provider: str | None = None
    ai_api_key: SecretStr | None = None
    ai_model: str | None = None
    ai_base_url: str | None = None
    ai_max_tokens: int = 2048
    ai_timeout_seconds: float = 30.0
    ai_monthly_budget_usd: float | None = None


    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.TEXT

    # ── Computed helpers ─────────────────────────────────────────────────────
    @property
    def is_development(self) -> bool:
        return self.app_env == AppEnvironment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnvironment.PRODUCTION

    @property
    def ai_enabled(self) -> bool:
        """True when AI provider is configured (or mock provider is active)."""
        if self.ai_provider == "mock":
            return True
        return self.ai_provider is not None and self.ai_api_key is not None

    # ── Validators ───────────────────────────────────────────────────────────
    @field_validator("secret_key")
    @classmethod
    def secret_key_not_placeholder(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw in ("replace-with-a-real-secret-key-before-use", "changeme", ""):
            raise ValueError(
                "UITP_SECRET_KEY is a placeholder value. "
                "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(raw) < 32:
            raise ValueError("UITP_SECRET_KEY must be at least 32 characters.")
        return v

    @field_validator("log_level")
    @classmethod
    def log_level_valid(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"UITP_LOG_LEVEL must be one of {sorted(valid)}, got '{v}'.")
        return upper

    @model_validator(mode="after")
    def _check_production_deps(self) -> "Settings":
        # Redis required for Milestone 2 worker queue — warn but don't block M1.
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton.

    In tests: call get_settings.cache_clear() before overriding env vars.
    """
    return Settings()  # type: ignore[call-arg]
