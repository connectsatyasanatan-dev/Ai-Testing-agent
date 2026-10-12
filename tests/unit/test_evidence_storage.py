"""
tests/unit/test_evidence_storage.py
===================================
Unit tests for evidence storage abstraction, filesystem safety, and sensitive-data redaction.
(Task P1-06B).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

try:
    import pytest
except ImportError:
    class _MockMarker:
        def __getattr__(self, name):
            return lambda fn: fn
    class _MockPytest:
        mark = _MockMarker()
        @staticmethod
        def fixture(*args, **kwargs):
            return lambda fn: fn
        @staticmethod
        def raises(exc):
            class _RaisesContext:
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc_val, tb):
                    if exc_type is None:
                        raise AssertionError(f"Expected exception {exc.__name__} was not raised")
                    return issubclass(exc_type, exc)
            return _RaisesContext()
    pytest = _MockPytest()

from uitp_evidence.exceptions import (
    ArtifactNotFoundError,
    CorruptedArtifactError,
    PayloadSizeLimitExceededError,
    StorageSecurityError,
)
from uitp_evidence.redaction import (
    REDACTED_MASK,
    RedactionEngine,
    default_redaction_engine,
)
from uitp_evidence.storage import (
    LocalFilesystemEvidenceStorage,
)


@pytest.mark.unit
class TestLocalFilesystemEvidenceStorage:
    @pytest.fixture(autouse=True)
    def setup_tmp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="uitp_storage_test_")
        self.storage = LocalFilesystemEvidenceStorage(
            base_dir=self.tmp_dir,
            max_size_bytes=1024 * 1024,  # 1 MB
        )
        yield
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_store_and_retrieve_valid_artifact(self) -> None:
        run_id = "run-12345"
        artifact_id = "art-67890"
        payload = b'{"status": "ok", "message": "all tests passed"}'

        object_key = self.storage.store(
            run_id=run_id,
            artifact_id=artifact_id,
            content=payload,
            content_type="application/json",
        )

        assert object_key == f"runs/{run_id}/{artifact_id}.bin"
        assert self.storage.exists(object_key) is True

        # Retrieve content
        retrieved = self.storage.read(object_key)
        assert retrieved == payload

        # Check metadata
        meta = self.storage.get_metadata(object_key)
        assert meta["size_bytes"] == len(payload)

    def test_checksum_verification_and_corruption_detection(self) -> None:
        run_id = "run-integrity"
        artifact_id = "art-integrity"
        payload = b"sensitive-audit-trail-bytes"
        expected_sha = hashlib.sha256(payload).hexdigest()

        object_key = self.storage.store(
            run_id=run_id,
            artifact_id=artifact_id,
            content=payload,
        )

        # Successful verification with matching checksum
        data = self.storage.read(object_key, expected_checksum=expected_sha)
        assert data == payload

        # Tampered content triggers CorruptedArtifactError
        with pytest.raises(CorruptedArtifactError):
            self.storage.read(object_key, expected_checksum="0" * 64)

    def test_missing_payload_raises_not_found(self) -> None:
        with pytest.raises(ArtifactNotFoundError):
            self.storage.read("runs/nonexistent/missing.bin")

    def test_enforce_payload_size_limit(self) -> None:
        limited_storage = LocalFilesystemEvidenceStorage(
            base_dir=self.tmp_dir,
            max_size_bytes=50,  # 50 bytes limit
        )
        small_payload = b"small payload under limit"
        oversized_payload = b"A" * 51

        # Small succeeds
        key = limited_storage.store(
            run_id="run-size",
            artifact_id="art-small",
            content=small_payload,
        )
        assert limited_storage.exists(key)

        # Oversized rejected with PayloadSizeLimitExceededError
        with pytest.raises(PayloadSizeLimitExceededError):
            limited_storage.store(
                run_id="run-size",
                artifact_id="art-big",
                content=oversized_payload,
            )

    def test_block_path_traversal_and_absolute_paths(self) -> None:
        dangerous_keys = [
            "../escape.bin",
            "..\\escape.bin",
            "/etc/passwd",
            "C:\\Windows\\System32\\calc.exe",
            "runs/../../../secret.txt",
            "runs/sub/../../etc/shadow",
            "runs/null\0byte.bin",
        ]
        for bad_key in dangerous_keys:
            with pytest.raises(StorageSecurityError):
                self.storage.read(bad_key)

            with pytest.raises(StorageSecurityError):
                self.storage.exists(bad_key)

            with pytest.raises(StorageSecurityError):
                self.storage.delete(bad_key)

    def test_unsafe_identifiers_blocked_in_store(self) -> None:
        payload = b"test"
        bad_ids = [
            ("../traversal", "art-1"),
            ("run-1", "../art-traversal"),
            ("run/slash", "art-1"),
            ("run-1", "art\\slash"),
            ("run;inject", "art-1"),
        ]
        for run_id, art_id in bad_ids:
            with pytest.raises(StorageSecurityError):
                self.storage.store(
                    run_id=run_id,
                    artifact_id=art_id,
                    content=payload,
                )

    def test_atomic_write_cleans_up_on_failure(self) -> None:
        payload = b"partial write test"
        run_id = "run-atomic"
        artifact_id = "art-atomic"

        with patch("os.replace", side_effect=OSError("Disk full simulation")):
            with pytest.raises(Exception):
                self.storage.store(
                    run_id=run_id,
                    artifact_id=artifact_id,
                    content=payload,
                )

        # Confirm target artifact was not left behind as corrupted partial file
        object_key = f"runs/{run_id}/{artifact_id}.bin"
        assert self.storage.exists(object_key) is False

    def test_delete_artifact(self) -> None:
        run_id = "run-del"
        artifact_id = "art-del"
        key = self.storage.store(
            run_id=run_id,
            artifact_id=artifact_id,
            content=b"to be deleted",
        )
        assert self.storage.exists(key) is True

        deleted = self.storage.delete(key)
        assert deleted is True
        assert self.storage.exists(key) is False

        # Deleting already-deleted key returns False
        assert self.storage.delete(key) is False


@pytest.mark.unit
class TestSensitiveDataRedaction:
    def setup_method(self) -> None:
        self.engine = RedactionEngine(max_string_len=100)

    def test_redact_sensitive_headers(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer super-secret-token",
            "Cookie": "session=secret_cookie_val",
            "Set-Cookie": "auth=token123",
            "X-API-Key": "my-api-key-999",
            "Accept": "application/json",
        }
        cleaned, was_redacted = self.engine.redact_headers(headers)
        assert was_redacted is True
        assert cleaned["Authorization"] == REDACTED_MASK
        assert cleaned["Cookie"] == REDACTED_MASK
        assert cleaned["Set-Cookie"] == REDACTED_MASK
        assert cleaned["X-API-Key"] == REDACTED_MASK
        assert cleaned["Content-Type"] == "application/json"
        assert cleaned["Accept"] == "application/json"

    def test_redact_url_credentials_and_query_params(self) -> None:
        url = "https://user:password@api.example.com/v1/data?token=secret123&api_key=xyz&safe=1"
        sanitized, was_redacted = self.engine.redact_url(url)
        assert was_redacted is True
        assert "password" not in sanitized
        assert "secret123" not in sanitized
        assert "xyz" not in sanitized
        assert ("token=%5BREDACTED%5D" in sanitized or f"token={REDACTED_MASK}" in sanitized)
        assert ("api_key=%5BREDACTED%5D" in sanitized or f"api_key={REDACTED_MASK}" in sanitized)
        assert "safe=1" in sanitized

    def test_redact_nested_json_structures(self) -> None:
        payload = {
            "user": {
                "name": "Alice",
                "password": "plaintext-password",
                "tokens": [{"access_token": "jwt-token-1"}, {"refresh_token": "jwt-token-2"}],
            },
            "meta": {"client_secret": "top-secret"},
            "public_info": "safe",
        }
        cleaned, was_redacted = self.engine.redact_data(payload)
        assert was_redacted is True
        assert cleaned["user"]["password"] == REDACTED_MASK
        assert cleaned["user"]["tokens"][0]["access_token"] == REDACTED_MASK
        assert cleaned["user"]["tokens"][1]["refresh_token"] == REDACTED_MASK
        assert cleaned["meta"]["client_secret"] == REDACTED_MASK
        assert cleaned["user"]["name"] == "Alice"
        assert cleaned["public_info"] == "safe"

    def test_redact_bearer_tokens_and_jwt_in_freeform_text(self) -> None:
        jwt_mock = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozG6P_fake_signature"
        text = f"Received headers: Bearer my-secret-token and jwt {jwt_mock} from server."
        cleaned, was_redacted = self.engine.redact_data(text)
        assert was_redacted is True
        assert "my-secret-token" not in cleaned
        assert jwt_mock not in cleaned
        assert REDACTED_MASK in cleaned

    def test_payload_length_truncation_bound(self) -> None:
        long_text = "A" * 150
        cleaned, was_redacted = self.engine.redact_data(long_text)
        assert was_redacted is True
        assert len(cleaned) < 150
        assert "[TRUNCATED 50 bytes]" in cleaned

    def test_redact_content_bytes_and_json(self) -> None:
        json_bytes = b'{"password": "secret", "user": "bob"}'
        sanitized_bytes, was_redacted = self.engine.redact_content(json_bytes, "application/json")
        assert was_redacted is True
        assert b'"password": "[REDACTED]"' in sanitized_bytes
        assert b'"user": "bob"' in sanitized_bytes
