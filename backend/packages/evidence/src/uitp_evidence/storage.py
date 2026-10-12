"""
uitp_evidence.storage
=====================
Storage abstractions and local filesystem implementation for test evidence artifacts.

Security Guarantees:
  - All artifacts are stored under a strictly validated application-controlled root.
  - Path traversal attempts, symlink escapes, and absolute path injections are rejected with StorageSecurityError.
  - Filename / object key generation is controlled and opaque; public APIs never expose physical paths.
  - Bounded storage sizes enforced prior to persistent writes.
  - Atomic write strategy prevents partially-written or corrupt artifacts from appearing as complete.
  - Integrity verification via SHA-256 checksums.
"""
from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
import uuid

from uitp_evidence.exceptions import (
    ArtifactNotFoundError,
    CorruptedArtifactError,
    EvidenceError,
    PayloadSizeLimitExceededError,
    StorageSecurityError,
)

logger = logging.getLogger(__name__)

# Default maximum artifact size: 10 MB
DEFAULT_MAX_ARTIFACT_SIZE_BYTES = 10 * 1024 * 1024


class EvidenceStorage(ABC):
    """Abstract interface for storing and retrieving test evidence artifacts."""

    @abstractmethod
    def store(
        self,
        *,
        run_id: str,
        artifact_id: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        """Store an artifact payload and return an opaque internal storage reference (object_key)."""

    @abstractmethod
    def read(self, object_key: str, expected_checksum: str | None = None) -> bytes:
        """Read artifact payload content by storage reference."""

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        """Check whether an artifact exists on storage."""

    @abstractmethod
    def delete(self, object_key: str) -> bool:
        """Delete an artifact payload from storage. Returns True if deleted, False if already absent."""

    @abstractmethod
    def get_metadata(self, object_key: str) -> dict[str, Any]:
        """Get safe storage metadata (e.g. size_bytes) for an artifact."""


class LocalFilesystemEvidenceStorage(EvidenceStorage):
    """Secure local filesystem implementation of EvidenceStorage."""

    def __init__(
        self,
        base_dir: str | Path,
        max_size_bytes: int = DEFAULT_MAX_ARTIFACT_SIZE_BYTES,
    ) -> None:
        try:
            self.base_dir = Path(base_dir).resolve()
            # Fail closed if base_dir is root or cannot be established safely
            if str(self.base_dir) in ("/", "\\") or not self.base_dir.is_absolute():
                raise StorageSecurityError(f"Insecure or invalid base directory: {base_dir}")
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise StorageSecurityError(f"Failed to initialize evidence storage root: {exc}") from exc

        self.max_size_bytes = max_size_bytes

    def _validate_and_resolve_key(self, object_key: str) -> Path:
        """Resolve object_key against base_dir and ensure strict path containment."""
        if not object_key or not isinstance(object_key, str):
            raise StorageSecurityError("Artifact storage key must be a non-empty string.")

        # Disallow null bytes and traversal tokens
        if "\0" in object_key:
            raise StorageSecurityError("Null bytes in storage key are prohibited.")

        raw_path = Path(object_key)
        if raw_path.is_absolute():
            raise StorageSecurityError("Absolute paths are prohibited in storage keys.")

        # Check for path traversal components
        for part in raw_path.parts:
            if part in ("..", ".", "~") or ":" in part:
                raise StorageSecurityError(f"Path traversal sequence '{part}' detected in storage key.")

        resolved_target = (self.base_dir / raw_path).resolve()

        # Strict containment check
        try:
            if not resolved_target.is_relative_to(self.base_dir):
                raise StorageSecurityError("Storage key resolves outside application storage root.")
        except AttributeError:
            # Fallback for Python < 3.9 (should not occur on 3.12, but defensive)
            if not str(resolved_target).startswith(str(self.base_dir)):
                raise StorageSecurityError("Storage key resolves outside application storage root.")

        return resolved_target

    def store(
        self,
        *,
        run_id: str,
        artifact_id: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        """Store artifact payload using an atomic write strategy.

        Returns an internal storage key reference (e.g., 'runs/<run_id>/<artifact_id>.bin').
        """
        # 1. Enforce size limit
        if len(content) > self.max_size_bytes:
            raise PayloadSizeLimitExceededError(
                f"Artifact size ({len(content)} bytes) exceeds configured limit ({self.max_size_bytes} bytes)."
            )

        # 2. Validate IDs against safe naming pattern
        for identifier, name in [(run_id, "run_id"), (artifact_id, "artifact_id")]:
            if not identifier or not all(c.isalnum() or c in ("-", "_") for c in identifier):
                raise StorageSecurityError(f"Invalid characters in {name}: '{identifier}'")

        # 3. Construct opaque internal storage key
        object_key = f"runs/{run_id}/{artifact_id}.bin"
        target_path = self._validate_and_resolve_key(object_key)

        # 4. Atomic write via temp file
        target_dir = target_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt" and hasattr(os, "chmod"):
            try:
                os.chmod(target_dir, 0o700)
            except OSError:
                pass

        temp_filename = f"{target_path.name}.tmp.{uuid.uuid4().hex}"
        temp_path = target_dir / temp_filename

        try:
            with open(temp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            if os.name != "nt" and hasattr(os, "chmod"):
                try:
                    os.chmod(temp_path, 0o600)
                except OSError:
                    pass

            os.replace(temp_path, target_path)
            logger.debug("Successfully stored artifact %s (%d bytes)", object_key, len(content))
            return object_key

        except Exception as exc:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            if isinstance(exc, (StorageSecurityError, PayloadSizeLimitExceededError)):
                raise
            raise EvidenceError(f"Failed to persist artifact payload: {exc}") from exc

    def read(self, object_key: str, expected_checksum: str | None = None) -> bytes:
        """Read artifact payload content and verify SHA-256 checksum if provided."""
        target_path = self._validate_and_resolve_key(object_key)

        if not target_path.exists() or not target_path.is_file():
            raise ArtifactNotFoundError(f"Artifact payload not found for key: {object_key}")

        try:
            content = target_path.read_bytes()
        except OSError as exc:
            raise EvidenceError(f"Failed to read artifact payload: {exc}") from exc

        if expected_checksum:
            actual_checksum = hashlib.sha256(content).hexdigest()
            if actual_checksum.lower() != expected_checksum.lower():
                raise CorruptedArtifactError(
                    f"Integrity check failed: expected checksum {expected_checksum}, got {actual_checksum}"
                )

        return content

    def exists(self, object_key: str) -> bool:
        """Check whether an artifact payload exists."""
        target_path = self._validate_and_resolve_key(object_key)
        return target_path.is_file()

    def delete(self, object_key: str) -> bool:
        """Safely delete an artifact payload."""
        target_path = self._validate_and_resolve_key(object_key)

        if not target_path.exists():
            return False

        if not target_path.is_file():
            raise StorageSecurityError("Cannot delete non-file storage target.")

        try:
            target_path.unlink()
            return True
        except OSError as exc:
            raise EvidenceError(f"Failed to delete artifact payload: {exc}") from exc

    def get_metadata(self, object_key: str) -> dict[str, Any]:
        """Return safe metadata for stored payload."""
        target_path = self._validate_and_resolve_key(object_key)

        if not target_path.exists() or not target_path.is_file():
            raise ArtifactNotFoundError(f"Artifact payload not found for key: {object_key}")

        stat = target_path.stat()
        return {
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        }
