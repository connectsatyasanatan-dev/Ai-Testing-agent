"""
tests/unit/test_config.py
=========================
Tests for application configuration (P0-03 acceptance criteria).

Verifies:
- Missing required settings raise a clear error at startup.
- Placeholder secret key is rejected.
- Secret values are not exposed through repr/str (never logged).
- AI provider is optional (returns False for ai_enabled when absent).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# We construct Settings directly in each test rather than using the
# singleton from get_settings() to keep tests isolated.
from uitp_api.config import Settings


def _make_settings(**overrides: object) -> Settings:
    """Helper: create Settings with minimal valid values plus overrides."""
    defaults: dict[str, object] = {
        "app_env": "test",
        "secret_key": "a-valid-test-secret-key-with-32-chars!!",
        "database_url": "postgresql+asyncpg://user:pass@localhost/test",
    }
    for k, v in overrides.items():
        key = k.lower()
        if key.startswith("uitp_"):
            key = key[5:]
        defaults[key] = v
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestSettingsValidation:
    def test_valid_settings_accepted(self) -> None:
        s = _make_settings()
        assert s.app_env.value == "test"

    def test_missing_secret_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UITP_SECRET_KEY", raising=False)
        with pytest.raises((ValidationError, Exception)):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                database_url="postgresql+asyncpg://user:pass@localhost/test",
                app_env="test",
            )

    def test_missing_database_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UITP_DATABASE_URL", raising=False)
        with pytest.raises((ValidationError, Exception)):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                secret_key="a-valid-test-secret-key-with-32-chars!!",
                app_env="test",
            )

    def test_placeholder_secret_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="placeholder"):
            _make_settings(
                uitp_secret_key="replace-with-a-real-secret-key-before-use"
            )

    def test_short_secret_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="32 characters"):
            _make_settings(uitp_secret_key="tooshort")

    def test_invalid_log_level_rejected(self) -> None:
        with pytest.raises(ValidationError, match="LOG_LEVEL"):
            _make_settings(uitp_log_level="VERBOSE")


@pytest.mark.unit
class TestSecretSafety:
    def test_secret_key_not_in_repr(self) -> None:
        """Secret values must not appear in repr() — they would appear in logs."""
        s = _make_settings()
        representation = repr(s)
        # Pydantic SecretStr repr shows '**********', not the actual value.
        assert "a-valid-test-secret-key" not in representation

    def test_database_url_not_in_repr(self) -> None:
        s = _make_settings()
        representation = repr(s)
        assert "pass@localhost" not in representation


@pytest.mark.unit
class TestAIOptional:
    def test_ai_disabled_by_default(self) -> None:
        s = _make_settings()
        assert s.ai_enabled is False

    def test_ai_enabled_when_configured(self) -> None:
        s = _make_settings(
            uitp_ai_provider="openai",
            uitp_ai_api_key="sk-test-key-for-unit-tests-only-not-real",
        )
        assert s.ai_enabled is True

    def test_ai_provider_without_key_is_disabled(self) -> None:
        """Provider name alone is not enough — key must also be present."""
        s = _make_settings(uitp_ai_provider="openai")
        assert s.ai_enabled is False
