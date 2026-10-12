"""
uitp_api.services.evidence_service
==================================
Service orchestrating artifact redaction, persistent storage, authorization,
and audit lifecycle.

Responsibilities:
  - Redaction of sensitive data (headers, query parameters, tokens, credentials)
    before persistence.
  - Safe, atomic local filesystem storage via EvidenceStorage.
  - Opaque artifact metadata persistence (EvidenceArtifact model).
  - Strict project-scoped authorization (cross-project returns 404 to prevent enumeration).
  - Expiry enforcement based on configured 30-day retention policy (ADR-013).
  - Emitting ARTIFACT_ACCESSED audit events without leaking payloads or secrets.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.config import get_settings
from uitp_api.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.run import Run
from uitp_api.repositories.evidence_repo import EvidenceRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.repositories.run_repo import RunRepository
from uitp_api.services import audit_service
from uitp_contracts.enums import AuditEventType
from uitp_evidence.exceptions import (
    ArtifactExpiredError,
    ArtifactNotFoundError,
    CorruptedArtifactError,
    EvidenceError,
    PayloadSizeLimitExceededError,
    StorageSecurityError,
)
from uitp_evidence.redaction import default_redaction_engine
from uitp_evidence.storage import (
    DEFAULT_MAX_ARTIFACT_SIZE_BYTES,
    EvidenceStorage,
    LocalFilesystemEvidenceStorage,
)

logger = logging.getLogger(__name__)

# Cached storage instance
_storage_instance: EvidenceStorage | None = None


def get_evidence_storage() -> EvidenceStorage:
    """Retrieve or initialize the configured EvidenceStorage singleton."""
    global _storage_instance
    if _storage_instance is None:
        settings = get_settings()
        storage_path = settings.artifact_storage_path
        max_bytes = getattr(settings, "artifact_max_size_bytes", DEFAULT_MAX_ARTIFACT_SIZE_BYTES)
        _storage_instance = LocalFilesystemEvidenceStorage(
            base_dir=storage_path,
            max_size_bytes=max_bytes,
        )
    return _storage_instance


def set_evidence_storage(storage: EvidenceStorage | None) -> None:
    """Override storage instance (for unit and integration tests)."""
    global _storage_instance
    _storage_instance = storage


async def _assert_project_access(
    session: AsyncSession,
    project_id: str,
    actor_id: str,
    allowed_roles: tuple[str, ...],
) -> None:
    """Enforce tenant isolation. Non-members receive ResourceNotFoundError (404)."""
    project_repo = ProjectRepository(session)
    project = await project_repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    membership = await project_repo.get_membership(project_id, actor_id)
    if membership is None:
        raise ResourceNotFoundError("Project not found.")

    if membership.role not in allowed_roles:
        raise PermissionDeniedError(
            f"Insufficient permissions: requires one of {', '.join(allowed_roles)}."
        )


async def store_artifact(
    session: AsyncSession,
    *,
    run_id: str,
    step_attempt_id: str | None = None,
    artifact_type: str,
    content: bytes | str,
    content_type: str = "application/json",
    redaction_required: bool = True,
    storage: EvidenceStorage | None = None,
) -> EvidenceArtifact:
    """Redact, store, and record metadata for a test evidence artifact.

    Applies the default 30-day retention period (ADR-013).
    """
    settings = get_settings()
    active_storage = storage or get_evidence_storage()

    # 1. Apply sensitive data redaction
    if redaction_required:
        sanitized_bytes, was_redacted = default_redaction_engine.redact_content(
            content=content,
            content_type=content_type,
        )
        redaction_status = "redacted" if was_redacted else "not_required"
    else:
        if isinstance(content, str):
            sanitized_bytes = content.encode("utf-8")
        else:
            sanitized_bytes = content
        redaction_status = "not_required"

    # 2. Compute SHA-256 checksum
    checksum = hashlib.sha256(sanitized_bytes).hexdigest()

    # 3. Generate opaque UUIDv4 artifact ID
    import uuid

    artifact_id = str(uuid.uuid4())

    # 4. Persist to storage backend
    try:
        object_key = active_storage.store(
            run_id=run_id,
            artifact_id=artifact_id,
            content=sanitized_bytes,
            content_type=content_type,
        )
    except PayloadSizeLimitExceededError as exc:
        raise ValidationFailedError(str(exc)) from exc
    except StorageSecurityError as exc:
        logger.error("Security violation attempting to store artifact: %s", exc)
        raise ValidationFailedError("Invalid artifact parameters.") from exc
    except EvidenceError as exc:
        logger.exception("Failed to store artifact payload: %s", exc)
        raise

    # 5. Calculate retention timestamp (default 30 days per ADR-013)
    retention_days = getattr(settings, "artifact_retention_days", 30)
    retention_until = datetime.now(UTC) + timedelta(days=retention_days)

    # 6. Persist metadata in database
    evidence_repo = EvidenceRepository(session)
    artifact = EvidenceArtifact(
        id=artifact_id,
        run_id=run_id,
        step_attempt_id=step_attempt_id,
        type=artifact_type,
        object_key=object_key,
        checksum=checksum,
        redaction_status=redaction_status,
        retention_until=retention_until,
        size_bytes=len(sanitized_bytes),
        content_type=content_type,
    )
    evidence_repo.add(artifact)
    await session.flush()

    logger.info(
        "Stored evidence artifact %s for run %s (type=%s, bytes=%d, redaction=%s)",
        artifact.id,
        run_id,
        artifact_type,
        len(sanitized_bytes),
        redaction_status,
    )
    return artifact


async def get_artifact(
    session: AsyncSession,
    *,
    run_id: str,
    artifact_id: str,
    actor_id: str,
    storage: EvidenceStorage | None = None,
) -> tuple[EvidenceArtifact, bytes]:
    """Retrieve and authorize access to an evidence artifact.

    Enforces:
      - Project authorization (returns 404 for non-members to prevent tenant enumeration)
      - Run ownership
      - Retention expiry checks
      - Audit trail logging (ARTIFACT_ACCESSED)
    """
    run_repo = RunRepository(session)
    evidence_repo = EvidenceRepository(session)
    active_storage = storage or get_evidence_storage()

    # 1. Verify Run existence and tenant scoping
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")

    await _assert_project_access(
        session,
        project_id=run.project_id,
        actor_id=actor_id,
        allowed_roles=("owner", "maintainer", "viewer"),
    )

    # 2. Verify Artifact existence and run relationship
    artifact = await evidence_repo.get_by_id(artifact_id)
    if artifact is None or artifact.run_id != run_id:
        raise ResourceNotFoundError("Artifact not found.")

    # 3. Check retention expiry
    now = datetime.now(UTC)
    if artifact.retention_until is not None and artifact.retention_until < now:
        logger.info(
            "Artifact %s retention expired at %s (current time: %s)",
            artifact.id,
            artifact.retention_until,
            now,
        )
        raise ResourceNotFoundError("Artifact has expired.")

    # 4. Read payload from storage
    try:
        content = active_storage.read(
            artifact.object_key,
            expected_checksum=artifact.checksum,
        )
    except ArtifactNotFoundError:
        logger.warning(
            "Artifact metadata exists for %s but storage payload is missing.",
            artifact.id,
        )
        raise ResourceNotFoundError("Artifact payload is unavailable.")
    except CorruptedArtifactError as exc:
        logger.error("Artifact %s failed integrity checksum: %s", artifact.id, exc)
        raise ResourceNotFoundError("Artifact payload corrupted.")

    # 5. Record ARTIFACT_ACCESSED audit event (without payload or secrets)
    await audit_service.record(
        session,
        event_type=AuditEventType.ARTIFACT_ACCESSED,
        actor_id=actor_id,
        project_id=run.project_id,
        resource_type="artifact",
        resource_id=artifact.id,
        metadata={
            "run_id": run.id,
            "artifact_type": artifact.type,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
        },
    )

    return artifact, content


async def list_artifacts_for_run(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
) -> list[EvidenceArtifact]:
    """List all valid artifacts for a run."""
    run_repo = RunRepository(session)
    evidence_repo = EvidenceRepository(session)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")

    await _assert_project_access(
        session,
        project_id=run.project_id,
        actor_id=actor_id,
        allowed_roles=("owner", "maintainer", "viewer"),
    )

    artifacts = await evidence_repo.list_for_run(run_id)
    # Filter out expired artifacts from active retrieval
    now = datetime.now(UTC)
    active_artifacts = [
        a for a in artifacts
        if a.retention_until is None or a.retention_until >= now
    ]
    return active_artifacts


async def delete_artifact_payload(
    session: AsyncSession,
    *,
    artifact_id: str,
    storage: EvidenceStorage | None = None,
) -> bool:
    """Delete an underlying artifact payload from storage.

    Preserves Run, StepAttempt, and EvidenceArtifact metadata.
    """
    evidence_repo = EvidenceRepository(session)
    active_storage = storage or get_evidence_storage()

    artifact = await evidence_repo.get_by_id(artifact_id)
    if artifact is None:
        return False

    deleted = active_storage.delete(artifact.object_key)
    return deleted
