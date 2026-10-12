"""
uitp_api.routers.runs
=====================
API routes for test run execution, retrieval, and timeline inspection (Tasks P1-06A & API_CONTRACT.md).

Endpoints:
  - POST /api/v1/plans/{plan_id}/runs          — execute run against approved plan version
  - GET  /api/v1/runs/{run_id}                 — get run summary and step attempts
  - GET  /api/v1/runs/{run_id}/events          — get run timeline events
  - GET  /api/v1/projects/{project_id}/runs    — list runs for project
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.models.user import User
from uitp_api.services import run_service
from uitp_contracts.run import CreateRunRequest

logger = logging.getLogger(__name__)

plans_runs_router = APIRouter(prefix="/api/v1/plans/{plan_id}/runs", tags=["runs"])
runs_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
project_runs_router = APIRouter(prefix="/api/v1/projects/{project_id}/runs", tags=["runs"])


class StepAttemptResponse(BaseModel):
    id: str
    run_id: str
    step_id: str
    attempt_number: int
    status: str
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class RunResponse(BaseModel):
    id: str
    plan_id: str
    plan_version: int
    environment_id: str
    project_id: str
    status: str
    requested_by: str
    worker_id: str | None = None
    idempotency_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    step_attempts: list[StepAttemptResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RunEventResponse(BaseModel):
    id: str
    run_id: str
    step_id: str | None = None
    attempt_id: str | None = None
    event_type: str
    timestamp: datetime
    message: str | None = None
    event_metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class RunListResponse(BaseModel):
    items: list[RunResponse]
    total: int


@plans_runs_router.post("", response_model=RunResponse, status_code=201)
async def create_and_execute_run(
    plan_id: str,
    body: CreateRunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunResponse:
    """Execute a test run synchronously against an approved plan version.

    Rejects unapproved or modified plans with 422 PLAN_NOT_APPROVED.
    """
    transport = getattr(request.app.state, "custom_adapter_transport", None)
    run = await run_service.execute_run_sync(
        session,
        plan_id=plan_id,
        environment_id=body.environment_id,
        actor_id=current_user.id,
        plan_version=body.plan_version,
        idempotency_key=body.idempotency_key,
        transport=transport,
    )
    return RunResponse.model_validate(run)


@runs_router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunResponse:
    """Get run summary and step attempts."""
    run = await run_service.get_run_by_id(
        session,
        run_id=run_id,
        actor_id=current_user.id,
    )
    return RunResponse.model_validate(run)


@runs_router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def get_run_events(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[RunEventResponse]:
    """Get immutable timeline events emitted during a run."""
    events = await run_service.list_run_events(
        session,
        run_id=run_id,
        actor_id=current_user.id,
    )
    return [RunEventResponse.model_validate(e) for e in events]


@project_runs_router.get("", response_model=RunListResponse)
async def list_runs_for_project(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunListResponse:
    """List all test runs in a project."""
    runs = await run_service.list_runs_for_project(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    items = [RunResponse.model_validate(r) for r in runs]
    return RunListResponse(items=items, total=len(items))
