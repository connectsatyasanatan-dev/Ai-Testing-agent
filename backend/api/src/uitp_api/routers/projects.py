"""
uitp_api.routers.projects
==========================
POST   /api/v1/projects                          — create project
GET    /api/v1/projects                          — list projects accessible to current user
GET    /api/v1/projects/{project_id}             — get single project
PATCH  /api/v1/projects/{project_id}/archive     — soft-delete
GET    /api/v1/projects/{project_id}/members     — list members with roles
POST   /api/v1/projects/{project_id}/members     — add or update member role (owner only)
DELETE /api/v1/projects/{project_id}/members/{user_id} — remove member (owner only)
GET    /api/v1/projects/{project_id}/audit-events— list immutable audit logs for project
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from uitp_api.middleware.auth import get_current_user
from uitp_api.models.project import ProjectMembership
from uitp_api.models.user import User
from uitp_api.repositories.audit_repo import AuditRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.repositories.user_repo import UserRepository
from uitp_api.services import audit_service, project_service
from uitp_contracts.enums import AuditEventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    status: str
    owner_id: str

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int


class ProjectMemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    full_name: str | None
    role: str
    created_at: datetime


class ProjectMemberListResponse(BaseModel):
    items: list[ProjectMemberResponse]
    total: int


class AddProjectMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="viewer", pattern="^(owner|maintainer|viewer)$")


class AuditEventResponse(BaseModel):
    id: str
    event_type: str
    actor_id: str | None
    project_id: str | None
    resource_type: str | None
    resource_id: str | None
    timestamp: datetime
    metadata_json: dict[str, Any] | None
    metadata_redacted: bool

    model_config = {"from_attributes": True}


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectResponse:
    """Create a new project. The creator is automatically assigned the Owner role."""
    project = await project_service.create_project(
        session,
        name=body.name,
        description=body.description,
        owner_id=current_user.id,
        ip_address=_client_ip(request),
    )
    return ProjectResponse.model_validate(project)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectListResponse:
    """List all active projects the current user is a member of."""
    repo = ProjectRepository(session)
    projects = await repo.list_for_user(current_user.id)
    items = [ProjectResponse.model_validate(p) for p in projects]
    return ProjectListResponse(items=items, total=len(items))


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectResponse:
    """Get a single project. Returns 404 if the project doesn't exist or
    the user is not a member (to avoid leaking project existence)."""
    repo = ProjectRepository(session)

    # Verify membership first — return 404 for non-members.
    if not await repo.user_has_role(project_id, current_user.id, "owner", "maintainer", "viewer"):
        raise ResourceNotFoundError("Project not found.")

    project = await repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectResponse:
    """Soft-delete a project. Only owners can archive. Cannot be undone via API."""
    project = await project_service.archive_project(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        ip_address=_client_ip(request),
    )
    return ProjectResponse.model_validate(project)


# ── Member Management (P4-02 & P6-02) ─────────────────────────────────────────

@router.get("/{project_id}/members", response_model=ProjectMemberListResponse)
async def list_project_members(
    project_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectMemberListResponse:
    """List all members and assigned roles for a project. Returns 404 for non-members."""
    repo = ProjectRepository(session)
    if not await repo.user_has_role(project_id, current_user.id, "owner", "maintainer", "viewer"):
        raise ResourceNotFoundError("Project not found.")

    memberships = await repo.list_members(project_id)
    items = [
        ProjectMemberResponse(
            id=m.id,
            user_id=m.user_id,
            email=m.user.email if m.user else "unknown",
            full_name=m.user.full_name if m.user else None,
            role=m.role,
            created_at=m.created_at,
        )
        for m in memberships
    ]
    return ProjectMemberListResponse(items=items, total=len(items))


@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=201)
async def add_or_update_member(
    project_id: str,
    body: AddProjectMemberRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ProjectMemberResponse:
    """Add a new member or update role of an existing member. Restricted to project owners."""
    repo = ProjectRepository(session)
    if not await repo.user_has_role(project_id, current_user.id, "owner", "maintainer", "viewer"):
        raise ResourceNotFoundError("Project not found.")
    if not await repo.user_has_role(project_id, current_user.id, "owner"):
        raise PermissionDeniedError("Only project owners can manage members.")

    user_repo = UserRepository(session)
    target_user = await user_repo.get_by_email(body.email)
    if target_user is None:
        raise ResourceNotFoundError(f"User with email '{body.email}' not found.")

    existing_membership = await repo.get_membership(project_id, target_user.id)
    if existing_membership is not None:
        old_role = existing_membership.role
        existing_membership.role = body.role
        session.add(existing_membership)
        membership = existing_membership
        action_desc = f"role changed from {old_role} to {body.role}"
    else:
        membership = ProjectMembership(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=target_user.id,
            role=body.role,
        )
        session.add(membership)
        action_desc = f"added with role {body.role}"

    # Audit role_change (ARCHITECTURE.md §5a)
    await audit_service.record(
        session,
        event_type=AuditEventType.ROLE_CHANGE,
        actor_id=current_user.id,
        project_id=project_id,
        resource_type="membership",
        resource_id=membership.id,
        ip_address=_client_ip(request),
        metadata={
            "target_user_id": target_user.id,
            "target_email": target_user.email,
            "role": body.role,
            "action": action_desc,
        },
    )
    await session.commit()
    await session.refresh(membership)

    return ProjectMemberResponse(
        id=membership.id,
        user_id=target_user.id,
        email=target_user.email,
        full_name=target_user.full_name,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: str,
    user_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Remove a user from project memberships. Owners only. Cannot remove the last owner."""
    repo = ProjectRepository(session)
    if not await repo.user_has_role(project_id, current_user.id, "owner", "maintainer", "viewer"):
        raise ResourceNotFoundError("Project not found.")
    if not await repo.user_has_role(project_id, current_user.id, "owner"):
        raise PermissionDeniedError("Only project owners can manage members.")

    membership = await repo.get_membership(project_id, user_id)
    if membership is None:
        raise ResourceNotFoundError("Membership not found.")

    if membership.role == "owner":
        owners_count = await repo.count_owners(project_id)
        if owners_count <= 1:
            raise ConflictError("Cannot remove the last owner of the project.")

    await audit_service.record(
        session,
        event_type=AuditEventType.ROLE_CHANGE,
        actor_id=current_user.id,
        project_id=project_id,
        resource_type="membership",
        resource_id=membership.id,
        ip_address=_client_ip(request),
        metadata={
            "target_user_id": user_id,
            "action": "member_removed",
            "previous_role": membership.role,
        },
    )
    await session.delete(membership)
    await session.commit()


# ── Audit Trail (P6-02) ────────────────────────────────────────────────────────

@router.get("/{project_id}/audit-events", response_model=AuditEventListResponse)
async def list_project_audit_events(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> AuditEventListResponse:
    """List immutable audit log records for this project. Restricted to project members."""
    repo = ProjectRepository(session)
    if not await repo.user_has_role(project_id, current_user.id, "owner", "maintainer", "viewer"):
        raise ResourceNotFoundError("Project not found.")

    audit_repo = AuditRepository(session)
    events, total = await audit_repo.list_for_project(
        project_id,
        limit=limit,
        offset=offset,
        event_type=event_type,
    )
    items = [AuditEventResponse.model_validate(e) for e in events]
    return AuditEventListResponse(items=items, total=total)
