"""
tests/unit/test_environment_service.py
======================================
Unit tests for environment validation logic and URL syntax checks.
"""
from __future__ import annotations

import pytest

from uitp_api.routers.environments import (
    _validate_allowlist,
    _validate_secret_references,
    _validate_url_syntax,
)


@pytest.mark.unit
class TestEnvironmentValidation:
    def test_valid_http_and_https_urls(self) -> None:
        assert _validate_url_syntax("http://localhost:8000") == "http://localhost:8000"
        assert _validate_url_syntax("https://api.example.com/v1") == "https://api.example.com/v1"
        assert _validate_url_syntax("https://192.0.2.1:8443") == "https://192.0.2.1:8443"

    def test_rejects_unsupported_schemes(self) -> None:
        with pytest.raises(ValueError, match="http or https scheme"):
            _validate_url_syntax("ftp://files.example.com")
        with pytest.raises(ValueError, match="http or https scheme"):
            _validate_url_syntax("file:///etc/passwd")

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(ValueError, match="valid host"):
            _validate_url_syntax("http://")

    def test_rejects_embedded_user_credentials(self) -> None:
        with pytest.raises(ValueError, match="embedded user credentials"):
            _validate_url_syntax("https://admin:secret123@api.example.com")

    def test_rejects_cloud_metadata_urls(self) -> None:
        with pytest.raises(ValueError, match="(?i)cloud metadata"):
            _validate_url_syntax("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(ValueError, match="(?i)metadata hostname"):
            _validate_url_syntax("http://metadata.google.internal/computeMetadata/v1/")

    def test_valid_allowlist(self) -> None:
        assert _validate_allowlist(None) is None
        assert _validate_allowlist(["api.example.com:443", "AUTH.EXAMPLE.COM"]) == [
            "api.example.com:443",
            "auth.example.com",
        ]

    def test_rejects_cloud_metadata_in_allowlist(self) -> None:
        with pytest.raises(ValueError, match="cloud metadata"):
            _validate_allowlist(["169.254.169.254:80"])
        with pytest.raises(ValueError, match="prohibited metadata hostname"):
            _validate_allowlist(["metadata.google.internal"])

    def test_valid_secret_references(self) -> None:
        refs = {"api_key": "vault://projects/p1/keys/key", "stripe": "env:STRIPE_SECRET"}
        assert _validate_secret_references(refs) == refs
        assert _validate_secret_references(None) is None

    def test_rejects_empty_secret_keys_or_values(self) -> None:
        with pytest.raises(ValueError, match="non-empty strings"):
            _validate_secret_references({"": "vault://key"})
        with pytest.raises(ValueError, match="non-empty identifier strings"):
            _validate_secret_references({"key": "  "})
