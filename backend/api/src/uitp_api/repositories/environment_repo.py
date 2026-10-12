"""
uitp_api.repositories.environment_repo
======================================
Repository for project-scoped test environment entities.
Enforces project scoping in database queries. Transaction ownership is retained
by the calling service or unit of work.
"""
from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.environment import Environment
from uitp_api.repositories.base import BaseRepository


class EnvironmentRepository(BaseRepository[Environment]):
    model_class = Environment

    async def get_by_id_and_project(
        self, environment_id: str, project_id: str
    ) -> Environment | None:
        """Fetch an environment ensuring strict project scoping in the SQL query."""
        stmt = select(Environment).where(
            and_(
                Environment.id == environment_id,
                Environment.project_id == project_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self, project_id: str, *, include_archived: bool = False
    ) -> list[Environment]:
        """List environments belonging to project_id.

        By default, soft-deleted ('archived') environments are excluded.
        """
        conditions = [Environment.project_id == project_id]
        if not include_archived:
            conditions.append(Environment.status == "active")

        stmt = (
            select(Environment)
            .where(and_(*conditions))
            .order_by(Environment.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
