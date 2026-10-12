"""Project and membership repository."""
from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.project import Project, ProjectMembership
from uitp_api.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    model_class = Project

    async def list_for_user(self, user_id: str) -> list[Project]:
        """Return all active projects the user is a member of."""
        stmt = (
            select(Project)
            .join(ProjectMembership, ProjectMembership.project_id == Project.id)
            .where(
                and_(
                    ProjectMembership.user_id == user_id,
                    Project.status == "active",
                )
            )
            .order_by(Project.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_membership(
        self, project_id: str, user_id: str
    ) -> ProjectMembership | None:
        stmt = select(ProjectMembership).where(
            and_(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == user_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def user_has_role(
        self, project_id: str, user_id: str, *roles: str
    ) -> bool:
        """Return True if user has any of the given roles on this project."""
        membership = await self.get_membership(project_id, user_id)
        if membership is None:
            return False
        return membership.role in roles
