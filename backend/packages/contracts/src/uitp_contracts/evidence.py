"""
uitp_contracts.evidence
=======================
Schemas for evidence artifacts and their metadata.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.enums import EvidenceType


class EvidenceArtifactSchema(BaseModel):
    """Metadata record for a stored evidence artifact.

    The artifact content is retrieved separately via the artifacts API.
    Raw object-store credentials are never exposed here (SECURITY.md §4).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """UUIDv4 artifact identifier. Non-guessable; authorization still required."""

    run_id: str
    step_attempt_id: str | None = None

    type: EvidenceType
    checksum: str | None = None
    """SHA-256 hex digest of the artifact content."""

    redaction_status: str = "pending"
    """'pending' | 'redacted' | 'failed' | 'not_required'"""

    retention_until: datetime | None = None
    created_at: datetime

    size_bytes: int | None = None
    content_type: str | None = None


class ArtifactDownloadResponse(BaseModel):
    """Response for artifact metadata/download endpoint."""

    model_config = ConfigDict(frozen=True)

    artifact: EvidenceArtifactSchema
    download_url: str | None = None
    """Short-lived signed URL for object storage download, if available."""

    inline_content: str | None = None
    """Base64-encoded content for small text artifacts (e.g. short logs)."""
