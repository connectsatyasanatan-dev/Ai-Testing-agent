"""
uitp_api.routers.plans
======================
Routes for creating, versioning, retrieving, and approving declarative test plans.

POST /api/v1/projects/{project_id}/plans         — create new plan draft (v=1)
GET  /api/v1/projects/{project_id}/plans         — list plans in project
GET  /api/v1/plans/{plan_id}                     — get latest plan version
GET  /api/v1/plans/{plan_id}/versions            — list all versions of a plan
GET  /api/v1/plans/{plan_id}/versions/{version}  — get specific plan version
POST /api/v1/plans/{plan_id}/versions            — create new version of plan
POST /api/v1/plans/{plan_id}/approve             — approve exact plan version
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.plan import TestPlan
from uitp_api.models.user import User
from uitp_api.services import ai_plan_service, plan_service
from uitp_api.services.rate_limiter import check_ai_generate_rate_limit
from uitp_contracts.plan import (
    ApprovePlanRequest,
    CreatePlanRequest,
    CreatePlanVersionRequest,
    GeneratePlanRequest,
    GeneratePlanResponse,
    PlanValidationResponse,
    PlanVersionsResponse,
    PlanVersionSummary,
    RejectPlanRequest,
    SubmitReviewRequest,
    TestStepSchema,
    UpdatePlanRequest,
    ValidatePlanRequest,
)

logger = logging.getLogger(__name__)

project_plans_router = APIRouter(prefix="/api/v1/projects/{project_id}/plans", tags=["plans"])
plans_router = APIRouter(prefix="/api/v1/plans", tags=["plans"])


# ── Response Schemas ──────────────────────────────────────────────────────────

class PlanStepResponse(BaseModel):
    step_id: str
    step_order: int
    adapter_type: str
    action: str
    inputs: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] | None = None
    timeout_seconds: float
    retry_policy: dict[str, Any] | None = None
    idempotency_class: str
    risk_level: str
    description: str | None = None

    model_config = {"from_attributes": True}


class PlanResponse(BaseModel):
    id: str
    plan_id: str
    project_id: str
    name: str
    version: int
    adapter_type: str
    status: str
    description: str | None = None
    requires_approval: bool = True
    created_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[PlanStepResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PlanListResponse(BaseModel):
    items: list[PlanResponse]
    total: int


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Project-Scoped Plan Endpoints ─────────────────────────────────────────────

@project_plans_router.post("", response_model=PlanResponse, status_code=201)
async def create_plan(
    project_id: str,
    body: CreatePlanRequest,
    request: Request,
    _: None = Depends(require_project_role("owner", "maintainer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Create a new declarative plan draft (version 1)."""
    plan = await plan_service.create_plan(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        name=body.name,
        adapter_type=body.adapter_type,
        steps=body.steps,
        description=body.description,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@project_plans_router.get("", response_model=PlanListResponse)
async def list_plans(
    project_id: str,
    status: str | None = None,
    search: str | None = None,
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanListResponse:
    """List plans in the project, returning the latest version for each."""
    plans = await plan_service.list_plans_for_project(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        status_filter=status,
        search_query=search,
    )
    items = [PlanResponse.model_validate(p) for p in plans]
    return PlanListResponse(items=items, total=len(items))


@project_plans_router.post("/validate", response_model=PlanValidationResponse)
async def validate_candidate_plan(
    project_id: str,
    body: ValidatePlanRequest,
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanValidationResponse:
    """Dry-run validation of a candidate test plan before saving."""
    return await plan_service.validate_plan_steps(
        session,
        adapter_type=body.adapter_type,
        steps=body.steps,
        environment_id=body.environment_id,
        project_id=project_id,
    )


@project_plans_router.post("/generate", response_model=GeneratePlanResponse, status_code=201)
async def generate_plan_draft(
    project_id: str,
    body: GeneratePlanRequest,
    request: Request,
    _: None = Depends(require_project_role("owner", "maintainer")),
    _rate_limit: None = Depends(check_ai_generate_rate_limit),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> GeneratePlanResponse:
    """Generate a structured draft test plan from natural language intent or specification.

    Requires Owner or Maintainer role. AI generated plans are always saved as drafts
    with requires_approval=True and must be approved before execution.
    """
    return await ai_plan_service.generate_plan(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        request=body,
        ip_address=_client_ip(request),
    )


# ── Global Plan Endpoints ─────────────────────────────────────────────────────

@plans_router.get("/{plan_id}", response_model=PlanResponse)
async def get_latest_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Get the current latest version of a plan."""
    plan = await plan_service.get_plan_latest(
        session,
        plan_id=plan_id,
        actor_id=current_user.id,
    )
    return PlanResponse.model_validate(plan)


@plans_router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: str,
    body: UpdatePlanRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Update a plan.

    If the latest version is already approved, creates a new draft version to preserve immutability.
    If the latest version is in draft, pending_review, or rejected, updates it in place.
    """
    plan, _ = await plan_service.update_plan(
        session,
        plan_id=plan_id,
        actor_id=current_user.id,
        steps=body.steps,
        name=body.name,
        description=body.description,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@plans_router.get("/{plan_id}/versions", response_model=PlanVersionsResponse)
async def list_plan_versions(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanVersionsResponse:
    """List version history of a plan."""
    root_id, versions = await plan_service.list_plan_versions(
        session,
        plan_id=plan_id,
        actor_id=current_user.id,
    )
    summaries = [
        PlanVersionSummary(
            version=v.version,
            status=v.status,
            name=v.name,
            step_count=len(v.steps),
            approved_by=v.approved_by,
            approved_at=v.approved_at.isoformat() if v.approved_at else None,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions
    ]
    return PlanVersionsResponse(plan_id=root_id, versions=summaries)


@plans_router.get("/{plan_id}/versions/{version}", response_model=PlanResponse)
async def get_plan_version(
    plan_id: str,
    version: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Get a specific immutable version of a plan."""
    plan = await plan_service.get_plan_version(
        session,
        plan_id=plan_id,
        version=version,
        actor_id=current_user.id,
    )
    return PlanResponse.model_validate(plan)


@plans_router.put("/{plan_id}/versions/{version}", response_model=PlanResponse)
async def update_plan_version(
    plan_id: str,
    version: int,
    body: UpdatePlanRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Update an exact plan version. Rejects modifications to approved versions with 409 Conflict."""
    plan = await plan_service.update_plan_version(
        session,
        plan_id=plan_id,
        version=version,
        actor_id=current_user.id,
        steps=body.steps,
        name=body.name,
        description=body.description,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@plans_router.post("/{plan_id}/versions", response_model=PlanResponse, status_code=201)
async def create_plan_version(
    plan_id: str,
    body: CreatePlanVersionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Create a new version for an existing plan."""
    plan = await plan_service.create_plan_version(
        session,
        plan_id=plan_id,
        actor_id=current_user.id,
        steps=body.steps,
        name=body.name,
        description=body.description,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@plans_router.post("/{plan_id}/validate", response_model=PlanValidationResponse)
async def validate_plan(
    plan_id: str,
    body: ValidatePlanRequest | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanValidationResponse:
    """Validate a plan's steps (dry-run, no execution)."""
    if body and body.steps:
        return await plan_service.validate_plan_steps(
            session,
            adapter_type=body.adapter_type,
            steps=body.steps,
            environment_id=body.environment_id,
        )
    # Validate existing plan's latest version
    plan = await plan_service.get_plan_latest(session, plan_id=plan_id, actor_id=current_user.id)
    steps_schema = [
        TestStepSchema(
            step_id=s.step_id,
            adapter_type=s.adapter_type,
            action=s.action,
            inputs=s.inputs or {},
            assertions=s.assertions or [],
            timeout_seconds=s.timeout_seconds,
            retry_policy=s.retry_policy or {},
            idempotency_class=s.idempotency_class,
            risk_level=s.risk_level,
            description=s.description,
        )
        for s in plan.steps
    ]
    return await plan_service.validate_plan_steps(
        session,
        adapter_type=plan.adapter_type,
        steps=steps_schema,
        project_id=plan.project_id,
    )


@plans_router.post("/{plan_id}/submit-review", response_model=PlanResponse)
async def submit_plan_review(
    plan_id: str,
    body: SubmitReviewRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Submit a draft plan for review."""
    plan = await plan_service.submit_plan_for_review(
        session,
        plan_id=plan_id,
        version=body.version,
        actor_id=current_user.id,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@plans_router.post("/{plan_id}/approve", response_model=PlanResponse)
async def approve_plan(
    plan_id: str,
    body: ApprovePlanRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Approve an exact immutable version of a plan.

    Protected against TOCTOU race conditions by atomic conditional verification.
    """
    plan = await plan_service.approve_plan(
        session,
        plan_id=plan_id,
        version=body.version,
        actor_id=current_user.id,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


@plans_router.post("/{plan_id}/reject", response_model=PlanResponse)
async def reject_plan(
    plan_id: str,
    body: RejectPlanRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PlanResponse:
    """Reject a plan under review or in draft."""
    plan = await plan_service.reject_plan(
        session,
        plan_id=plan_id,
        version=body.version,
        actor_id=current_user.id,
        reason=body.reason,
        ip_address=_client_ip(request),
    )
    return PlanResponse.model_validate(plan)


class ArchivePlanRequest(BaseModel):
    version: int | None = None


@plans_router.post("/{plan_id}/archive")
async def archive_plan(
    plan_id: str,
    body: ArchivePlanRequest | None = None,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Soft-delete a plan or specific version."""
    version = body.version if body else None
    await plan_service.archive_plan(
        session,
        plan_id=plan_id,
        version=version,
        actor_id=current_user.id,
        ip_address=_client_ip(request) if request else None,
    )
    return {"message": "Plan archived successfully", "plan_id": plan_id, "version": version}
