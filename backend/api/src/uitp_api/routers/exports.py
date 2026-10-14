"""
uitp_api.routers.exports
========================
API routes for exporting evidence-based execution logs and final reports.

Endpoints:
  - POST /api/v1/runs/{run_id}/export        — Trigger generation of execution_log.json & report.md
  - GET  /api/v1/runs/{run_id}/export/report — Retrieve generated report.md directly
  - GET  /api/v1/runs/{run_id}/export/log    — Retrieve generated execution_log.json directly
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.config import get_settings
from uitp_api.database import get_async_session
from uitp_api.exceptions import PermissionDeniedError, ResourceNotFoundError
from uitp_api.middleware.auth import get_current_user
from uitp_api.models.run import Run
from uitp_api.models.user import User
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services.report_export_service import export_run_execution_artifacts
from uitp_contracts.export import ExecutionLogSchema, ExportResult

router = APIRouter(prefix="/api/v1/runs/{run_id}/export", tags=["exports"])


async def _check_run_access(session: AsyncSession, run_id: str, actor_id: str) -> Run:
    """Verify run exists and user has membership in the owning project."""
    run = await session.get(Run, run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")

    project_repo = ProjectRepository(session)
    membership = await project_repo.get_membership(run.project_id, actor_id)
    if membership is None:
        # Mask project existence by returning 404
        raise ResourceNotFoundError("Run not found.")

    return run


@router.post("", response_model=ExportResult)
async def trigger_run_export(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ExportResult:
    """Generate and persist execution_log.json and report.md from execution data."""
    await _check_run_access(session, run_id, current_user.id)
    return await export_run_execution_artifacts(session, run_id=run_id)


@router.get("/report")
async def get_run_report(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Retrieve human-readable report.md for the given test run."""
    run = await _check_run_access(session, run_id, current_user.id)
    settings = get_settings()
    base_dir = Path(settings.export_output_dir).resolve()
    report_file = base_dir / "runs" / run.id / "report.md"

    if not report_file.exists():
        # Generate on-demand if not already generated
        await export_run_execution_artifacts(session, run_id=run.id)

    if not report_file.exists():
        raise ResourceNotFoundError("Report not available.")

    content = report_file.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'inline; filename="report_{run.id}.md"'},
    )


@router.get("/log", response_model=ExecutionLogSchema)
async def get_run_execution_log(
    run_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ExecutionLogSchema:
    """Retrieve versioned execution_log.json for the given test run."""
    run = await _check_run_access(session, run_id, current_user.id)
    settings = get_settings()
    base_dir = Path(settings.export_output_dir).resolve()
    log_file = base_dir / "runs" / run.id / "execution_log.json"

    if not log_file.exists():
        await export_run_execution_artifacts(session, run_id=run.id)

    if not log_file.exists():
        raise ResourceNotFoundError("Execution log not available.")

    raw = log_file.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    return ExecutionLogSchema.model_validate(parsed)
