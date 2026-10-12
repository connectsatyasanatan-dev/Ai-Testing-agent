"""
uitp_api.routers.projects
==========================
POST   /api/v1/projects              — create project
GET    /api/v1/projects              — list projects accessible to current user
GET    /api/v1/projects/{project_id} — get single project
PATCH  /api/v1/projects/{project_id}/archive — soft-delete
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.exceptions import ResourceNotFoundError, PermissionDeniedError
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.user import User
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import project_service

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
