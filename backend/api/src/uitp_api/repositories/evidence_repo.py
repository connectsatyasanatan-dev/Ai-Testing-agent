"""
uitp_api.repositories.evidence_repo
===================================
Async repository for EvidenceArtifact models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.repositories.base import BaseRepository


class EvidenceRepository(BaseRepository[EvidenceArtifact]):
    """Data access repository for evidence artifact records."""

    model_class = EvidenceArtifact

    async def list_for_run(self, run_id: str) -> list[EvidenceArtifact]:
        """List all evidence artifacts recorded for a specific run."""
        stmt = (
            select(EvidenceArtifact)
            .where(EvidenceArtifact.run_id == run_id)
            .order_by(asc(EvidenceArtifact.created_at))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_step_attempt(self, step_attempt_id: str) -> list[EvidenceArtifact]:
        """List all evidence artifacts linked to a specific step attempt."""
        stmt = (
            select(EvidenceArtifact)
            .where(EvidenceArtifact.step_attempt_id == step_attempt_id)
            .order_by(asc(EvidenceArtifact.created_at))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_expired(self, current_time: datetime) -> list[EvidenceArtifact]:
        """List artifacts whose retention period has expired."""
        stmt = (
            select(EvidenceArtifact)
            .where(
                EvidenceArtifact.retention_until.is_not(None),
                EvidenceArtifact.retention_until < current_time,
            )
            .order_by(asc(EvidenceArtifact.retention_until))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
