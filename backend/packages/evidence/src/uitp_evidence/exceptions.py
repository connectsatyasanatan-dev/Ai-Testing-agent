"""
uitp_evidence.exceptions
========================
Typed exception hierarchy for evidence and artifact storage operations.
"""
from __future__ import annotations


class EvidenceError(Exception):
    """Base exception for all evidence and storage errors."""


class ArtifactNotFoundError(EvidenceError):
    """Raised when an artifact identifier or storage key does not exist."""


class ArtifactExpiredError(EvidenceError):
    """Raised when an artifact's retention period has elapsed."""


class StorageSecurityError(EvidenceError):
    """Raised when an unsafe path traversal, symlink escape, or invalid key is detected."""


class PayloadSizeLimitExceededError(EvidenceError):
    """Raised when an artifact payload exceeds maximum allowable size limits."""


class CorruptedArtifactError(EvidenceError):
    """Raised when an artifact payload checksum or integrity check fails."""
