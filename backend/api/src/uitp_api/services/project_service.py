"""
uitp_api.services.project_service
===================================
Business logic for creating and managing projects.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.exceptions import ConflictError, PermissionDeniedError, ResourceNotFoundError
from uitp_api.models.project import Project, ProjectMembership
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import audit_service
from uitp_contracts.enums import AuditEventType


async def create_project(
    session: AsyncSession,
    *,
    name: str,
    description: str | None,
    owner_id: str,
    ip_address: str | None = None,
) -> Project:
    """Create a new project and add the creator as Owner.

    Commits the session on success.
    """
    project = Project(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        owner_id=owner_id,
        status="active",
    )
    session.add(project)

    # Owner membership is always created alongside the project.
    membership = ProjectMembership(
        id=str(uuid.uuid4()),
        project_id=project.id,
        user_id=owner_id,
        role="owner",
    )
    session.add(membership)

    await audit_service.record(
        session,
        event_type=AuditEventType.PROJECT_CREATED,
        actor_id=owner_id,
        project_id=project.id,
        resource_type="project",
        resource_id=project.id,
        ip_address=ip_address,
        metadata={"project_name": name},
    )

    await session.commit()
    await session.refresh(project)
    return project


async def archive_project(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    ip_address: str | None = None,
) -> Project:
    """Soft-delete a project by setting status='archived'.

    Only owners may archive. Hard deletes are forbidden (would orphan runs).
    """
    repo = ProjectRepository(session)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise ResourceNotFoundError("Project not found.")

    is_owner = await repo.user_has_role(project_id, actor_id, "owner")
    if not is_owner:
        raise PermissionDeniedError("Only project owners can archive a project.")

    if project.status == "archived":
        raise ConflictError("Project is already archived.")

    project.status = "archived"  # type: ignore[assignment]
    session.add(project)

    await audit_service.record(
        session,
        event_type=AuditEventType.PROJECT_ARCHIVED,
        actor_id=actor_id,
        project_id=project_id,
        resource_type="project",
        resource_id=project_id,
        ip_address=ip_address,
    )

    await session.commit()
    await session.refresh(project)
    return project
