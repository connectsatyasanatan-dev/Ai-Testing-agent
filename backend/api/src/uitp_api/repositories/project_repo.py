from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

    async def list_members(self, project_id: str) -> list[ProjectMembership]:
        """Return all memberships for a project with joined user data."""
        stmt = (
            select(ProjectMembership)
            .where(ProjectMembership.project_id == project_id)
            .options(selectinload(ProjectMembership.user))
            .order_by(ProjectMembership.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_owners(self, project_id: str) -> int:
        """Return the number of owners currently in the project."""
        stmt = select(func.count(ProjectMembership.id)).where(
            and_(
                ProjectMembership.project_id == project_id,
                ProjectMembership.role == "owner",
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)
