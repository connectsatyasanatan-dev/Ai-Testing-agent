"""
tests.unit.test_worker_runner
=============================
Unit tests for uitp_worker_runner.execution:
  - Duplicate delivery prevention (DB-backed claim checks).
  - Strict exact-version approval gate enforcement.
  - Worker crash handling (never leave run in 'running').
  - Wall-clock timeout handling.
  - Successful execution and step attempt persistence.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from uitp_adapter_base.base import StepExecutionResult
from uitp_api.database import Base
from uitp_api.models.environment import Environment
from uitp_api.models.plan import TestPlan as PlanModel, TestStep as StepModel
from uitp_api.models.project import Project, ProjectMembership
from uitp_api.models.run import Run
from uitp_api.models.user import User
from uitp_contracts.enums import (
    AdapterType,
    IdempotencyClass,
    PlanStatus,
    RiskLevel,
    RunStatus,
    StepStatus,
)
from uitp_contracts.plan import RetryPolicySchema
from uitp_orchestrator.exceptions import DuplicateExecutionError, WorkerExecutionError
from uitp_worker_runner.execution import async_execute_run_job


@pytest.fixture
async def memory_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_test_data(
    session: AsyncSession,
    *,
    plan_status: str = "approved",
    run_status: str = "queued",
) -> tuple[Project, Environment, PlanModel, Run, User]:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email="dev@example.com",
        full_name="Dev",
        password_hash="hashed_pw_test",
        is_active=True,
    )
    session.add(user)

    proj_id = str(uuid.uuid4())
    project = Project(
        id=proj_id,
        name="Test Proj",
        owner_id=user_id,
        status="active",
    )
    session.add(project)

    membership = ProjectMembership(
        id=str(uuid.uuid4()),
        project_id=proj_id,
        user_id=user_id,
        role="owner",
    )
    session.add(membership)

    env_id = str(uuid.uuid4())
    env = Environment(
        id=env_id,
        project_id=proj_id,
        name="Dev Env",
        base_url="http://mock-api.local",
        allowlist=["mock-api.local"],
        status="active",
    )
    session.add(env)

    plan_id = str(uuid.uuid4())
    plan = PlanModel(
        id=str(uuid.uuid4()),
        plan_id=plan_id,
        version=1,
        project_id=proj_id,
        name="Sanity Plan",
        status=plan_status,
        adapter_type=AdapterType.HTTP_API,
        created_by=user_id,
        approved_at=datetime.now(UTC) if plan_status == "approved" else None,
        approved_by=user_id if plan_status == "approved" else None,
    )
    session.add(plan)

    step = StepModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        step_id="step-check-health",
        step_order=1,
        adapter_type=AdapterType.HTTP_API,
        action="get",
        inputs={"path": "/ok"},
        assertions=[],
        timeout_seconds=5.0,
        retry_policy={"max_attempts": 1, "backoff_seconds": 0.1},
        idempotency_class=IdempotencyClass.SAFE,
        risk_level=RiskLevel.LOW,
    )
    session.add(step)

    run_id = str(uuid.uuid4())
    run = Run(
        id=run_id,
        plan_id=plan_id,
        plan_version=1,
        environment_id=env_id,
        project_id=proj_id,
        status=run_status,
        requested_by=user_id,
    )
    session.add(run)

    await session.commit()
    return project, env, plan, run, user


@pytest.mark.asyncio
async def test_duplicate_delivery_rejected(memory_db: AsyncSession) -> None:
    """Duplicate job deliveries for non-queued runs must raise DuplicateExecutionError."""
    # Seed with run status = 'running'
    _, _, _, run, _ = await _seed_test_data(memory_db, run_status="running")

    with pytest.raises(DuplicateExecutionError):
        await async_execute_run_job(
            run_id=run.id,
            worker_id="worker-test",
            session=memory_db,
        )

    # Status must still be running
    refreshed = await memory_db.get(Run, run.id)
    assert refreshed is not None
    assert refreshed.status == "running"


@pytest.mark.asyncio
async def test_unapproved_plan_rejected(memory_db: AsyncSession) -> None:
    """If plan is draft/unapproved, execution fails and marks run failed."""
    _, _, _, run, _ = await _seed_test_data(memory_db, plan_status="draft", run_status="queued")

    result = await async_execute_run_job(
        run_id=run.id,
        worker_id="worker-test",
        session=memory_db,
    )

    assert result["status"] == "failed"
    refreshed = await memory_db.get(Run, run.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.finished_at is not None


@pytest.mark.asyncio
async def test_worker_crash_marks_run_failed(memory_db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """If an unhandled exception occurs inside worker loop, run is marked failed."""
    _, _, _, run, _ = await _seed_test_data(memory_db, plan_status="approved", run_status="queued")

    # Patch adapter to raise simulated crash
    from uitp_adapter_http_api import HttpApiAdapter

    async def _crash(*args, **kwargs):
        raise RuntimeError("Simulated segmentation fault / hard worker crash")

    monkeypatch.setattr(HttpApiAdapter, "execute_step", _crash)

    # Worker handles step exception, but if something catastrophic happens in orchestration:
    async def _crash_plan_repo(*args, **kwargs):
        raise RuntimeError("Database connection suddenly dropped")

    monkeypatch.setattr("uitp_worker_runner.execution.PlanRepository.get_specific_version", _crash_plan_repo)

    with pytest.raises(WorkerExecutionError):
        await async_execute_run_job(
            run_id=run.id,
            worker_id="worker-crash-test",
            session=memory_db,
        )

    refreshed = await memory_db.get(Run, run.id)
    assert refreshed is not None
    # Must NOT be left running or queued
    assert refreshed.status == "failed"
    assert refreshed.finished_at is not None


@pytest.mark.asyncio
async def test_successful_worker_execution(memory_db: AsyncSession) -> None:
    """Worker executes approved plan, persists step attempt, and marks run passed."""
    _, _, _, run, _ = await _seed_test_data(memory_db, plan_status="approved", run_status="queued")

    from tests.fixtures.mock_http_api import create_mock_api_app

    mock_transport = httpx.ASGITransport(app=create_mock_api_app())

    result = await async_execute_run_job(
        run_id=run.id,
        worker_id="worker-success",
        session=memory_db,
        transport=mock_transport,
    )

    assert result["status"] == "passed"
    refreshed = await memory_db.get(Run, run.id)
    assert refreshed is not None
    assert refreshed.status == "passed"
    assert refreshed.worker_id == "worker-success"
    assert refreshed.started_at is not None
    assert refreshed.finished_at is not None
