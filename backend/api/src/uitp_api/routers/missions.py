"""
uitp_api.routers.missions
=========================
API routes for autonomous multi-agent testing missions and DAG lifecycle:
- POST /api/v1/projects/{project_id}/missions/plan  — Supervisor/Planner decomposes specs into task DAG
- GET  /api/v1/missions/{mission_id}/dag            — Retrieve task DAG and dependencies
- POST /api/v1/missions/{mission_id}/approve        — Approve DAG plan version for execution
- GET  /api/v1/runs/{run_id}/evaluation             — Evaluator agent requirement coverage report
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.exceptions import PermissionDeniedError, ResourceNotFoundError
from uitp_api.middleware.auth import get_current_user, require_project_role
from uitp_api.models.dag import DAGDependency, DAGTask
from uitp_api.models.plan import TestPlan
from uitp_api.models.project import Project
from uitp_api.models.user import User
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import audit_service, multi_agent_service
from uitp_contracts.enums import AuditEventType
from uitp_contracts.openapi import AnalyzeOpenAPIRequest, OpenAPIAnalysisResult
from uitp_contracts.plan import (
    DAGTaskSchema,
    MissionDAGPlan,
    MissionEvaluationReport,
    MultiAgentMissionRequest,
)

logger = logging.getLogger(__name__)

project_missions_router = APIRouter(prefix="/api/v1/projects/{project_id}/missions", tags=["missions"])
missions_router = APIRouter(prefix="/api/v1/missions", tags=["missions"])
runs_evaluation_router = APIRouter(prefix="/api/v1/runs/{run_id}/evaluation", tags=["missions"])


@project_missions_router.post("/plan", response_model=MissionDAGPlan, status_code=201)
async def plan_multi_agent_mission(
    project_id: str,
    request_body: MultiAgentMissionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MissionDAGPlan:
    """Supervisor/Planner agent decomposes requirements and scenarios into a task DAG."""
    return await multi_agent_service.plan_mission_dag(
        session,
        project_id=project_id,
        actor_id=current_user.id,
        request=request_body,
    )


@project_missions_router.post("/analyze-openapi", response_model=OpenAPIAnalysisResult)
async def analyze_openapi(
    project_id: str,
    request_body: AnalyzeOpenAPIRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> OpenAPIAnalysisResult:
    """Deterministic OpenAPI 3.x analysis and test candidate extraction without LLM generation."""
    project_repo = ProjectRepository(session)
    project = await project_repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    membership = await project_repo.get_membership(project_id, current_user.id)
    if membership is None:
        raise ResourceNotFoundError("Project not found.")

    from uitp_api.services.openapi_service import analyze_openapi_specification
    return analyze_openapi_specification(request_body.openapi_spec)


@missions_router.get("/{mission_id}/dag", response_model=MissionDAGPlan)
async def get_mission_dag(
    mission_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MissionDAGPlan:
    """Retrieve mission plan DAG tasks and dependencies."""
    plan = await session.get(TestPlan, mission_id)
    if plan is None or plan.status == "archived":
        raise ResourceNotFoundError("Mission plan not found.")

    # Project access check
    project_repo = ProjectRepository(session)
    membership = await project_repo.get_membership(plan.project_id, current_user.id)
    if membership is None:
        raise ResourceNotFoundError("Mission plan not found.")

    tasks_query = select(DAGTask).where(DAGTask.plan_id == plan.id)
    db_tasks = (await session.execute(tasks_query)).scalars().all()

    deps_query = select(DAGDependency).where(DAGDependency.plan_id == plan.id)
    db_deps = (await session.execute(deps_query)).scalars().all()

    child_parent_map: dict[str, list[str]] = {}
    for dep in db_deps:
        child_parent_map.setdefault(dep.child_task_id, []).append(dep.parent_task_id)

    parsed_tasks: list[DAGTaskSchema] = []
    for dt in db_tasks:
        parsed_tasks.append(
            DAGTaskSchema(
                task_id=dt.task_id,
                name=dt.name,
                assigned_role=dt.assigned_role,
                adapter_type=dt.adapter_type,
                depends_on=child_parent_map.get(dt.task_id, []),
                steps=dt.steps,
                exports=dt.exports,
                timeout_seconds=dt.timeout_seconds,
                requirement_refs=dt.requirement_refs,
            )
        )

    return MissionDAGPlan(
        mission_id=plan.id,
        name=plan.name,
        description=plan.description,
        tasks=parsed_tasks,
        requires_approval=plan.requires_approval,
    )


@missions_router.post("/{mission_id}/approve")
async def approve_mission_dag(
    mission_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Approve mission DAG plan version for execution."""
    plan = await session.get(TestPlan, mission_id)
    if plan is None or plan.status == "archived":
        raise ResourceNotFoundError("Mission plan not found.")

    project_repo = ProjectRepository(session)
    membership = await project_repo.get_membership(plan.project_id, current_user.id)
    if membership is None:
        raise ResourceNotFoundError("Mission plan not found.")
    if membership.role not in ("owner", "maintainer"):
        raise PermissionDeniedError("Only owners and maintainers can approve mission plans.")

    now = datetime.now(UTC)
    plan.status = "approved"
    plan.approved_by = current_user.id
    plan.approved_at = now

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_APPROVED,
        actor_id=current_user.id,
        project_id=plan.project_id,
        resource_type="mission_dag_plan",
        resource_id=mission_id,
        metadata={"version": plan.version, "approver_id": current_user.id},
    )

    await session.commit()
    return {"mission_id": plan.id, "status": "approved", "approved_at": now.isoformat()}


@runs_evaluation_router.get("", response_model=MissionEvaluationReport)
async def get_mission_evaluation_report(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MissionEvaluationReport:
    """Retrieve evidence-grounded requirement coverage and root-cause report from Evaluator agent."""
    return await multi_agent_service.generate_mission_evaluation(
        session,
        run_id=run_id,
        actor_id=current_user.id,
    )
