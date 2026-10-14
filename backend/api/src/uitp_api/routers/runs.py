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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.models.user import User
from uitp_api.repositories.environment_repo import EnvironmentRepository
from uitp_api.repositories.plan_repo import PlanRepository
from uitp_api.services import run_service
from uitp_contracts.run import CreateRunRequest
from uitp_orchestrator import QueueError, QueueUnavailableError

logger = logging.getLogger(__name__)

plans_runs_router = APIRouter(prefix="/api/v1/plans/{plan_id}/runs", tags=["runs"])
runs_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
project_runs_router = APIRouter(prefix="/api/v1/projects/{project_id}/runs", tags=["runs"])
project_dashboard_router = APIRouter(prefix="/api/v1/projects/{project_id}/dashboard", tags=["dashboard"])


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
    action: str | None = None
    duration_ms: float | None = None
    assertions: list[dict[str, Any]] | None = None
    failure_category: str | None = None

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
    plan_name: str | None = None
    adapter_type: str | None = None
    environment_name: str | None = None
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
    limit: int = 50
    offset: int = 0


class CancelRunRequest(BaseModel):
    reason: str | None = None



class DashboardMetricsResponse(BaseModel):
    project_id: str
    total_plans: int
    total_runs: int
    status_counts: dict[str, int]
    adapter_breakdown: dict[str, int]
    recent_runs: list[RunResponse]
    recent_activity: list[RunEventResponse]

    model_config = ConfigDict(from_attributes=True)


@plans_runs_router.post("", response_model=RunResponse, status_code=201)
async def create_and_execute_run(
    plan_id: str,
    body: CreateRunRequest,
    request: Request,
    background: bool = Query(default=False, description="Enqueue for background execution via worker queue"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunResponse:
    """Execute a test run against an approved plan version.

    If background=True or body.background=True, enqueues the run to the background queue
    and returns immediately with status='queued'. Otherwise executes synchronously in-process.
    Rejects unapproved or modified plans with 422 PLAN_NOT_APPROVED.
    """
    idem_key = body.idempotency_key or request.headers.get("idempotency-key")
    is_async = background or getattr(body, "background", False) or getattr(request.app.state, "async_runs_enabled", False)
    if is_async:
        queue = getattr(request.app.state, "run_queue", None)
        try:
            run = await run_service.create_and_enqueue_run(
                session,
                plan_id=plan_id,
                environment_id=body.environment_id,
                actor_id=current_user.id,
                plan_version=body.plan_version,
                idempotency_key=idem_key,
                queue=queue,
            )
            return RunResponse.model_validate(run)
        except (QueueError, QueueUnavailableError) as q_err:
            raise HTTPException(
                status_code=503,
                detail=f"Queue transport failure: {q_err}",
            )

    transport = getattr(request.app.state, "custom_adapter_transport", None)
    web_adapter = getattr(request.app.state, "custom_web_adapter", None)
    run = await run_service.execute_run_sync(
        session,
        plan_id=plan_id,
        environment_id=body.environment_id,
        actor_id=current_user.id,
        plan_version=body.plan_version,
        idempotency_key=idem_key,
        transport=transport,
        web_adapter=web_adapter,
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
    resp = RunResponse.model_validate(run)

    # Enrich with plan details and step action/assertions if available
    plan_repo = PlanRepository(session)
    plan = await plan_repo.get_specific_version(run.plan_id, run.plan_version)
    step_map = {}
    if plan:
        resp.plan_name = plan.name
        resp.adapter_type = plan.adapter_type
        for s in plan.steps:
            step_map[s.step_id] = s

    env_repo = EnvironmentRepository(session)
    env = await env_repo.get_by_id(run.environment_id)
    if env:
        resp.environment_name = env.name

    # Enrich step attempts with action, duration, failure category, assertions
    enriched_attempts: list[StepAttemptResponse] = []
    for attempt in resp.step_attempts:
        step_model = step_map.get(attempt.step_id)
        action = step_model.action if step_model else None
        assertions_list = None
        if step_model and step_model.assertions:
            assertions_list = [
                a.model_dump() if hasattr(a, "model_dump") else (a if isinstance(a, dict) else {"type": str(a)})
                for a in step_model.assertions
            ]

        # Calculate duration_ms if timestamps available
        duration_ms = None
        if attempt.started_at and attempt.finished_at:
            duration_ms = max(0.0, round((attempt.finished_at - attempt.started_at).total_seconds() * 1000, 2))

        # Categorize failure
        failure_category = None
        err_msg_lower = (attempt.error_message or "").lower()
        err_code_upper = (attempt.error_code or "").upper()

        if attempt.status == "failed":
            if err_code_upper == "ASSERTION_FAILED" or "assertion" in err_msg_lower:
                failure_category = "assertion_failure"
            elif err_code_upper == "TIMEOUT" or "timeout" in err_msg_lower:
                failure_category = "timeout"
            elif err_code_upper == "POLICY_BLOCKED" or "ssrf" in err_msg_lower or "policy" in err_msg_lower:
                failure_category = "policy_blocked"
            else:
                failure_category = "execution_error"
        elif attempt.status == "cancelled" or err_code_upper == "CANCELLED":
            failure_category = "cancellation"
        elif attempt.status == "blocked":
            failure_category = "policy_blocked"

        enriched_attempt = attempt.model_copy(update={
            "action": action,
            "duration_ms": duration_ms,
            "assertions": assertions_list,
            "failure_category": failure_category,
        })
        enriched_attempts.append(enriched_attempt)

    resp.step_attempts = enriched_attempts
    return resp


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


@runs_router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    body: CancelRunRequest | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunResponse:
    """Request cancellation of an active or queued run (API_CONTRACT.md & Stage 2)."""
    reason = body.reason if body else None
    run = await run_service.cancel_run(
        session,
        run_id=run_id,
        actor_id=current_user.id,
        reason=reason,
    )
    return RunResponse.model_validate(run)


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=255)
    lease_seconds: int = Field(default=60, ge=10, le=600)


class RecoverStaleResponse(BaseModel):
    recovered_count: int
    recovered_run_ids: list[str]


@runs_router.post("/{run_id}/heartbeat", response_model=RunResponse)
async def heartbeat_run(
    run_id: str,
    body: HeartbeatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunResponse:
    """Renew worker lease for an active running execution (Task P3-02)."""
    run = await run_service.renew_run_lease(
        session,
        run_id=run_id,
        worker_id=body.worker_id,
        lease_seconds=body.lease_seconds,
    )
    return RunResponse.model_validate(run)


@runs_router.post("/recover-stale", response_model=RecoverStaleResponse)
async def recover_stale_runs(
    timeout_seconds: int = Query(default=300, ge=30, le=3600),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RecoverStaleResponse:
    """Scan and transition orphaned runs whose worker leases expired to failed (Task P3-02)."""
    recovered = await run_service.recover_stale_runs(session, timeout_seconds=timeout_seconds)
    return RecoverStaleResponse(
        recovered_count=len(recovered),
        recovered_run_ids=recovered,
    )



@project_runs_router.get("", response_model=RunListResponse)
async def list_runs_for_project(
    project_id: str,
    status: str | None = Query(default=None, description="Filter by run status"),
    plan_id: str | None = Query(default=None, description="Filter by plan ID"),
    adapter_type: str | None = Query(default=None, description="Filter by adapter type"),
    from_date: datetime | None = Query(default=None, description="Filter by minimum creation timestamp"),
    to_date: datetime | None = Query(default=None, description="Filter by maximum creation timestamp"),
    sort_by: str = Query(default="created_at", pattern="^(created_at|started_at|finished_at|status)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RunListResponse:
    """List test runs in a project with server-side filtering, sorting, and pagination."""
    runs, total, plan_map = await run_service.list_runs_for_project_filtered(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        status=status,
        plan_id=plan_id,
        adapter_type=adapter_type,
        from_date=from_date,
        to_date=to_date,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    items: list[RunResponse] = []
    for r in runs:
        resp = RunResponse.model_validate(r)
        if r.plan_id in plan_map:
            resp.plan_name, resp.adapter_type = plan_map[r.plan_id]
        items.append(resp)
    return RunListResponse(items=items, total=total, limit=limit, offset=offset)


@project_dashboard_router.get("", response_model=DashboardMetricsResponse)
async def get_project_dashboard(
    project_id: str,
    _: None = Depends(require_project_role("owner", "maintainer", "viewer")),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> DashboardMetricsResponse:
    """Get aggregated dashboard metrics for a project."""
    metrics = await run_service.get_project_dashboard_metrics(
        session,
        project_id=project_id,
        actor_id=current_user.id,
    )
    plan_map = metrics.get("plan_map", {})
    recent_run_responses: list[RunResponse] = []
    for r in metrics["recent_runs"]:
        resp = RunResponse.model_validate(r)
        if r.plan_id in plan_map:
            resp.plan_name, resp.adapter_type = plan_map[r.plan_id]
        recent_run_responses.append(resp)

    return DashboardMetricsResponse(
        project_id=metrics["project_id"],
        total_plans=metrics["total_plans"],
        total_runs=metrics["total_runs"],
        status_counts=metrics["status_counts"],
        adapter_breakdown=metrics["adapter_breakdown"],
        recent_runs=recent_run_responses,
        recent_activity=[RunEventResponse.model_validate(e) for e in metrics["recent_activity"]],
    )
