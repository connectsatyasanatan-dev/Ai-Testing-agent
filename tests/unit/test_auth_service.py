"""
tests/unit/test_auth_service.py
================================
Unit tests for password hashing and verification (no I/O).
"""
from __future__ import annotations

import pytest

from uitp_api.services.auth_service import hash_password, needs_rehash, verify_password


@pytest.mark.unit
class TestHashPassword:
    def test_produces_non_empty_hash(self) -> None:
        h = hash_password("correct-horse-battery-staple")
        assert h and len(h) > 20

    def test_two_hashes_of_same_password_differ(self) -> None:
        """Argon2id uses a random salt — hashes must not be identical."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2

    def test_empty_password_raises(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")

    def test_hash_does_not_contain_plaintext(self) -> None:
        plain = "super-secret-value"
        h = hash_password(plain)
        assert plain not in h


@pytest.mark.unit
class TestVerifyPassword:
    def test_correct_password_returns_true(self) -> None:
        h = hash_password("my-password-123")
        assert verify_password("my-password-123", h) is True

    def test_wrong_password_returns_false(self) -> None:
        h = hash_password("right-password")
        assert verify_password("wrong-password", h) is False

    def test_empty_password_returns_false(self) -> None:
        h = hash_password("valid-password-123")
        assert verify_password("", h) is False

    def test_invalid_hash_returns_false(self) -> None:
        assert verify_password("any", "not-a-valid-hash") is False


@pytest.mark.unit
class TestNeedsRehash:
    def test_fresh_hash_does_not_need_rehash(self) -> None:
        h = hash_password("fresh-hash-test")
        assert needs_rehash(h) is False
