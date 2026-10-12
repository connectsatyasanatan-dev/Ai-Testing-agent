"""
uitp_api.services.audit_service
================================
Writes immutable audit events.

SECURITY RULES:
  - metadata_json must be pre-sanitised — no secrets, no credential values.
  - If sanitisation fails, write the event with metadata_redacted=True and
    an empty metadata_json rather than dropping the event entirely.
  - Audit events are never updated or deleted.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.audit import AuditEvent
from uitp_contracts.enums import AuditEventType

logger = logging.getLogger(__name__)

# Patterns that must never appear in audit metadata.
_REDACT_KEYS = frozenset(
    {
        "password", "password_hash", "secret", "token", "api_key",
        "authorization", "cookie", "set-cookie", "x-api-key",
    }
)


def _sanitise(data: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
    """Strip sensitive keys from metadata. Returns (clean_data, was_redacted)."""
    if data is None:
        return None, False
    cleaned: dict[str, Any] = {}
    redacted = False
    for k, v in data.items():
        if k.lower() in _REDACT_KEYS:
            redacted = True
        else:
            cleaned[k] = v
    return cleaned or None, redacted


async def record(
    session: AsyncSession,
    *,
    event_type: AuditEventType | str,
    actor_id: str | None = None,
    project_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write a single audit event within the provided session.

    The caller is responsible for committing the session.
    This function only adds the event to the session — it does not commit.
    """
    clean_metadata, was_redacted = _sanitise(metadata)

    event = AuditEvent(
        event_type=str(event_type),
        actor_id=actor_id,
        project_id=project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        timestamp=datetime.now(UTC),
        metadata_json=clean_metadata,
        metadata_redacted=was_redacted,
    )
    session.add(event)
    logger.debug(
        "Audit event queued | type=%s actor=%s project=%s",
        event_type,
        actor_id,
        project_id,
    )
    return event
