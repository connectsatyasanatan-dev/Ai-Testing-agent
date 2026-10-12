"""
uitp_api.routers.environments
=============================
POST   /api/v1/projects/{project_id}/environments                  — register environment
GET    /api/v1/projects/{project_id}/environments                  — list project environments
GET    /api/v1/projects/{project_id}/environments/{environment_id} — get environment
PATCH  /api/v1/projects/{project_id}/environments/{environment_id} — update or soft-delete environment
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.user import User
from uitp_api.services import environment_service
from uitp_contracts.enums import EnvironmentStatus
from uitp_policy import normalize_url, parse_ip
from uitp_policy.ip import CLOUD_METADATA_NETWORKS, PROHIBITED_METADATA_HOSTNAMES

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments",
    tags=["environments"],
)


# ── URL and Secret Ref Validation ─────────────────────────────────────────────

def _validate_url_syntax(url_str: str) -> str:
    """Validate URL syntax and block cloud metadata targets without outbound DNS calls."""
    cleaned = url_str.strip()
    if not cleaned:
        raise ValueError("base_url cannot be empty.")
    try:
        parsed = urlsplit(cleaned)
    except Exception as exc:
        raise ValueError(f"Malformed base_url: {exc}") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("base_url must use http or https scheme.")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("base_url must include a valid host.")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not contain embedded user credentials.")

    norm_host = parsed.hostname.lower().rstrip(".")
    if norm_host in PROHIBITED_METADATA_HOSTNAMES:
        raise ValueError(f"Prohibited cloud metadata hostname '{norm_host}' is blocked by security policy.")

    parsed_ip = parse_ip(norm_host)
    if parsed_ip is not None:
        for net in CLOUD_METADATA_NETWORKS:
            if parsed_ip in net:
                raise ValueError("Cloud metadata addresses are blocked by SSRF policy.")

    return cleaned


def _validate_allowlist(allowlist: list[str] | None) -> list[str] | None:
    if allowlist is None:
        return None
    validated = []
    for item in allowlist:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Allowlist items must be non-empty strings.")
        cleaned = item.strip().lower()
        host_part = cleaned.split(":")[0]
        if host_part in PROHIBITED_METADATA_HOSTNAMES:
            raise ValueError(f"Allowlist cannot include prohibited metadata hostname '{host_part}'.")
        parsed_ip = parse_ip(host_part)
        if parsed_ip is not None:
            for net in CLOUD_METADATA_NETWORKS:
                if parsed_ip in net:
                    raise ValueError("Allowlist cannot include cloud metadata IP addresses.")
        validated.append(cleaned)
    return validated


def _validate_secret_references(refs: dict[str, str] | None) -> dict[str, str] | None:
    if refs is None:
        return None
    for name, ref_id in refs.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Secret reference keys must be non-empty strings.")
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise ValueError("Secret reference values must be non-empty identifier strings.")
    return refs


# ── Request / Response Schemas ────────────────────────────────────────────────

class CreateEnvironmentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., max_length=2048)
    allowlist: list[str] | None = Field(default=None)
    secret_refs: dict[str, str] | None = Field(default=None)
    description: str | None = Field(default=None, max_length=4096)

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, v: str) -> str:
        return _validate_url_syntax(v)

    @field_validator("allowlist")
    @classmethod
    def check_allowlist(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allowlist(v)

    @field_validator("secret_refs")
    @classmethod
    def check_secret_refs(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_secret_references(v)


class UpdateEnvironmentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    allowlist: list[str] | None = Field(default=None)
    secret_refs: dict[str, str] | None = Field(default=None)
    description: str | None = Field(default=None, max_length=4096)
    status: EnvironmentStatus | None = Field(default=None)

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_url_syntax(v)
        return v

    @field_validator("allowlist")
    @classmethod
    def check_allowlist(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allowlist(v)

    @field_validator("secret_refs")
    @classmethod
    def check_secret_refs(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_secret_references(v)


class EnvironmentResponse(BaseModel):
    id: str
    project_id: str
    name: str
    base_url: str
    allowlist: list[str] | None = None
    secret_refs: dict[str, str] | None = None
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EnvironmentListResponse(BaseModel):
    items: list[EnvironmentResponse]
    total: int


# ── Helper ────────────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=EnvironmentResponse, status_code=201)
async def create_environment(
    project_id: str,
    body: CreateEnvironmentRequest,
    request: Request,
    _: None = Depends(require_project_role("owner", "maintainer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EnvironmentResponse:
    """Register a new test environment in a project.

    Requires Owner or Maintainer role on the project.
    """
    env = await environment_service.create_environment(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        name=body.name,
        base_url=body.base_url,
        allowlist=body.allowlist,
        secret_refs=body.secret_refs,
        description=body.description,
        ip_address=_client_ip(request),
    )
    return EnvironmentResponse.model_validate(env)


@router.get("", response_model=EnvironmentListResponse)
async def list_environments(
    project_id: str,
    include_archived: bool = Query(default=False, description="Include archived environments if true"),
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EnvironmentListResponse:
    """List environments belonging to the project.

    By default, soft-deleted ('archived') environments are excluded.
    Accessible to Owner, Maintainer, and Viewer roles.
    """
    envs = await environment_service.list_environments(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        include_archived=include_archived,
    )
    items = [EnvironmentResponse.model_validate(e) for e in envs]
    return EnvironmentListResponse(items=items, total=len(items))


@router.get("/{environment_id}", response_model=EnvironmentResponse)
async def get_environment(
    project_id: str,
    environment_id: str,
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EnvironmentResponse:
    """Get a single active environment in the project."""
    env = await environment_service.get_environment(
        session,
        project_id=project_id,
        environment_id=environment_id,
        actor_id=current_user.id,
    )
    return EnvironmentResponse.model_validate(env)


@router.patch("/{environment_id}", response_model=EnvironmentResponse)
async def update_environment(
    project_id: str,
    environment_id: str,
    body: UpdateEnvironmentRequest,
    request: Request,
    _: None = Depends(require_project_role("owner", "maintainer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EnvironmentResponse:
    """Update environment configuration or soft-delete (status='archived').

    Requires Owner or Maintainer role.
    """
    update_data = body.model_dump(exclude_unset=True)
    env = await environment_service.update_environment(
        session,
        project_id=project_id,
        environment_id=environment_id,
        actor_id=current_user.id,
        update_data=update_data,
        ip_address=_client_ip(request),
    )
    return EnvironmentResponse.model_validate(env)


@router.delete("/{environment_id}", status_code=204)
async def delete_environment(
    project_id: str,
    environment_id: str,
    request: Request,
    _: None = Depends(require_project_role("owner", "maintainer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Soft-delete an environment (status='archived').

    Requires Owner or Maintainer role on the project.
    """
    await environment_service.delete_environment(
        session,
        project_id=project_id,
        environment_id=environment_id,
        actor_id=current_user.id,
        ip_address=_client_ip(request),
    )
