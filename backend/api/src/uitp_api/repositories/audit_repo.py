"""Audit event repository — append-only writes."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.audit import AuditEvent
from uitp_api.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditEvent]):
    model_class = AuditEvent

    # Audit events are written by AuditService, not this repo directly.
    # This class exists for read-side queries (admin, reporting).
