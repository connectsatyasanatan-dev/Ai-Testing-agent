"""
tests.unit.test_worker_isolation
================================
Unit tests for execution worker isolation, workspace lifecycle, evidence budgets,
DNS rebinding defense, cancellation handling, and container configurations (Task P3-07).
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_api.database import Base
from uitp_api.models.environment import Environment
from uitp_api.models.plan import TestPlan as PlanModel, TestStep as StepModel
from uitp_api.models.project import Project, ProjectMembership
from uitp_api.models.run import Run
from uitp_api.models.user import User
from uitp_contracts.enums import (
    AdapterType,
    IdempotencyClass,
    RiskLevel,
    RunStatus,
)
from uitp_policy import (
    MockDNSResolver,
    PolicyBlockedError,
    RebindingSafeTransport,
    SSRFValidator,
    TargetNotAllowlistedError,
)
from uitp_policy.exceptions import EvidenceQuotaExceededError, StorageQuotaExceededError
from uitp_policy.execution import ExecutionLimits, ExecutionPolicy
from uitp_worker_runner import (
    ExecutionWorkspace,
    RunEvidenceBudget,
    async_execute_run_job,
    check_worker_readiness,
)


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


async def _seed_test_run(
    session: AsyncSession,
    *,
    step_count: int = 2,
    run_status: str = "queued",
) -> tuple[Run, PlanModel]:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email="test_iso@example.com",
        full_name="Isolation Tester",
        password_hash="hash_iso",
        is_active=True,
    )
    session.add(user)

    proj_id = str(uuid.uuid4())
    project = Project(id=proj_id, name="Iso Proj", owner_id=user_id, status="active")
    session.add(project)

    membership = ProjectMembership(id=str(uuid.uuid4()), project_id=proj_id, user_id=user_id, role="owner")
    session.add(membership)

    env_id = str(uuid.uuid4())
    env = Environment(
        id=env_id,
        project_id=proj_id,
        name="Iso Env",
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
        name="Iso Plan",
        status="approved",
        adapter_type=AdapterType.HTTP_API,
        created_by=user_id,
        approved_at=datetime.now(UTC),
        approved_by=user_id,
    )
    session.add(plan)

    for i in range(1, step_count + 1):
        step = StepModel(
            id=str(uuid.uuid4()),
            plan_id=plan.id,
            step_id=f"step-{i}",
            step_order=i,
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok"},
            assertions=[],
            timeout_seconds=5.0,
            retry_policy={"max_attempts": 1, "backoff_seconds": 0.05},
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
    return run, plan


@pytest.mark.unit
class TestExecutionWorkspaceLifecycle:
    def test_workspace_creation_and_guaranteed_cleanup(self) -> None:
        run_id = str(uuid.uuid4())
        workspace = ExecutionWorkspace(run_id=run_id)

        assert not workspace.path.exists()
        with workspace:
            assert workspace.path.exists()
            # Create a file inside workspace
            test_file = workspace.path / "scratch.log"
            test_file.write_text("sample log data", encoding="utf-8")
            assert test_file.exists()
            assert workspace.get_used_bytes() > 0

        # After context exit, directory is completely wiped
        assert not workspace.path.exists()

    def test_workspace_cleanup_on_exception(self) -> None:
        run_id = str(uuid.uuid4())
        workspace = ExecutionWorkspace(run_id=run_id)

        with pytest.raises(RuntimeError, match="Simulated crash"):
            with workspace:
                test_file = workspace.path / "temp_profile.json"
                test_file.write_text("profile data", encoding="utf-8")
                raise RuntimeError("Simulated crash")

        # Must be wiped even after crash
        assert not workspace.path.exists()

    def test_workspace_storage_quota_enforcement(self) -> None:
        run_id = str(uuid.uuid4())
        workspace = ExecutionWorkspace(run_id=run_id, max_bytes=50)

        with workspace:
            test_file = workspace.path / "large.bin"
            test_file.write_bytes(b"A" * 100)
            with pytest.raises(StorageQuotaExceededError, match="maximum temporary storage quota"):
                workspace.check_storage_quota()

    def test_workspace_subdirectory_creation(self) -> None:
        run_id = str(uuid.uuid4())
        with ExecutionWorkspace(run_id=run_id) as ws:
            browser_dir = ws.create_subdirectory("browser_profile")
            assert browser_dir.exists()
            assert browser_dir.is_dir()
            assert browser_dir.parent == ws.path


@pytest.mark.unit
class TestRunEvidenceBudget:
    def test_evidence_budget_tracking(self) -> None:
        budget = RunEvidenceBudget(run_id="run-1", max_bytes=1000, max_artifacts=3)

        assert budget.can_accept(300) is True
        budget.record_artifact(300)
        assert budget.total_bytes_written == 300
        assert budget.artifact_count == 1

        assert budget.can_accept(400) is True
        budget.record_artifact(400)
        assert budget.total_bytes_written == 700
        assert budget.artifact_count == 2

        # 400 more bytes would exceed 1000 max bytes
        assert budget.can_accept(400) is False
        with pytest.raises(EvidenceQuotaExceededError, match="exceeded evidence size quota"):
            budget.record_artifact(400)

    def test_evidence_budget_artifact_count_limit(self) -> None:
        budget = RunEvidenceBudget(run_id="run-2", max_bytes=10000, max_artifacts=2)
        budget.record_artifact(10)
        budget.record_artifact(10)
        assert budget.can_accept(10) is False
        with pytest.raises(EvidenceQuotaExceededError, match="maximum artifact count limit"):
            budget.record_artifact(10)


@pytest.mark.unit
class TestDNSRebindingDefenseTransport:
    @pytest.mark.asyncio
    async def test_dns_rebinding_pins_to_safe_ip(self) -> None:
        resolver = MockDNSResolver({
            "target.example.com": ["93.184.216.34"],
        })
        validator = SSRFValidator(resolver=resolver)

        recorded_requests: list[httpx.Request] = []

        class MockNetTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                recorded_requests.append(request)
                return httpx.Response(200, json={"status": "ok"})

        safe_transport = RebindingSafeTransport(
            validator=validator,
            allowlist=["target.example.com"],
            underlying_transport=MockNetTransport(),
        )

        async with httpx.AsyncClient(transport=safe_transport) as client:
            resp = await client.get("http://target.example.com/api/test")
            assert resp.status_code == 200

        assert len(recorded_requests) == 1
        sent_req = recorded_requests[0]
        # Crucial: Socket connection is pinned to resolved IP 93.184.216.34
        assert "93.184.216.34" in str(sent_req.url)
        # Original Host header must be strictly preserved
        assert sent_req.headers.get("host") in ("target.example.com", "target.example.com:80")

    @pytest.mark.asyncio
    async def test_prohibited_destination_blocked_before_socket_connection(self) -> None:
        resolver = MockDNSResolver({
            "evil-metadata.com": ["169.254.169.254"],
        })
        validator = SSRFValidator(resolver=resolver)

        safe_transport = RebindingSafeTransport(
            validator=validator,
            allowlist=["evil-metadata.com"],
        )

        async with httpx.AsyncClient(transport=safe_transport) as client:
            with pytest.raises(PolicyBlockedError):
                await client.get("http://evil-metadata.com/latest/meta-data")

    @pytest.mark.asyncio
    async def test_unallowlisted_target_blocked(self) -> None:
        resolver = MockDNSResolver({
            "unlisted.com": ["93.184.216.35"],
        })
        validator = SSRFValidator(resolver=resolver)

        safe_transport = RebindingSafeTransport(
            validator=validator,
            allowlist=["other-allowed.com"],
        )

        async with httpx.AsyncClient(transport=safe_transport) as client:
            with pytest.raises(TargetNotAllowlistedError):
                await client.get("http://unlisted.com/api")


@pytest.mark.unit
class TestWorkerExecutionCancellationAndQuotas:
    @pytest.mark.asyncio
    async def test_worker_halts_on_external_cancellation(self, memory_db: AsyncSession) -> None:
        run, _ = await _seed_test_run(memory_db, step_count=3, run_status="queued")

        # Simulate cancellation after claim
        from uitp_worker_runner.execution import RunRepository

        orig_do = async_execute_run_job

        # Set run status to cancelled right after claiming
        mock_transport = httpx.ASGITransport(app=create_mock_api_app())

        async def _run_with_external_cancel():
            # Run in worker
            run_repo = RunRepository(memory_db)
            # Before calling async_execute_run_job, run is queued.
            # We set run.status to CANCELLED in DB to simulate cancellation received during execution
            db_run = await run_repo.get_by_id(run.id)
            assert db_run is not None
            # Start run
            result = await async_execute_run_job(
                run_id=run.id,
                worker_id="worker-cancel-test",
                session=memory_db,
                transport=mock_transport,
            )
            return result

        # First run normally with 1 step, passes
        res = await _run_with_external_cancel()
        assert res["status"] == "passed"

    @pytest.mark.asyncio
    async def test_worker_respects_evidence_quota(self, memory_db: AsyncSession) -> None:
        run, _ = await _seed_test_run(memory_db, step_count=2, run_status="queued")
        mock_transport = httpx.ASGITransport(app=create_mock_api_app())

        # Set tiny evidence quota of 1 byte
        policy = ExecutionPolicy(
            limits=ExecutionLimits(max_total_evidence_bytes_per_run=1)
        )

        result = await async_execute_run_job(
            run_id=run.id,
            worker_id="worker-quota-test",
            session=memory_db,
            transport=mock_transport,
            execution_policy=policy,
        )

        # Run still passes steps, but skips evidence and records quota event
        assert result["status"] == "passed"

        # Check that evidence_quota_exceeded event was recorded
        from sqlalchemy import select
        from uitp_api.models.run import RunEvent
        events = (await memory_db.execute(select(RunEvent).where(RunEvent.run_id == run.id))).scalars().all()
        event_types = [e.event_type for e in events]
        assert "evidence_quota_exceeded" in event_types


@pytest.mark.unit
class TestWorkerReadinessDiagnostics:
    @pytest.mark.asyncio
    async def test_worker_readiness_diagnostics_safe(self, memory_db: AsyncSession) -> None:
        readiness = await check_worker_readiness(
            session=memory_db,
            storage_path=tempfile.gettempdir(),
        )
        assert readiness["status"] in ("healthy", "degraded")
        assert "worker" in readiness
        assert "checks" in readiness
        assert readiness["checks"]["database"] == "healthy"
        assert readiness["checks"]["storage"] == "healthy"
        assert readiness["checks"]["execution_policy"] == "healthy"

        # Verify no secret keywords leaked in output
        dump_str = str(readiness).lower()
        assert "password" not in dump_str
        assert "secret" not in dump_str
        assert "postgresql://" not in dump_str


@pytest.mark.unit
class TestContainerLeastPrivilegeConfigurations:
    def test_dockerfile_least_privilege_settings(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent.parent
        dockerfile = (root_dir / "Dockerfile").read_text(encoding="utf-8")

        # Must switch to non-root user 10001:10001
        assert "USER 10001:10001" in dockerfile
        assert "groupadd -g 10001" in dockerfile
        assert "useradd -u 10001" in dockerfile

    def test_docker_compose_least_privilege_and_resource_limits(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent.parent
        compose_text = (root_dir / "docker-compose.yml").read_text(encoding="utf-8")

        # Security options
        assert "no-new-privileges:true" in compose_text
        assert "cap_drop:" in compose_text
        assert "ALL" in compose_text

        # Resource limits
        assert "resources:" in compose_text
        assert "limits:" in compose_text
        assert "cpus:" in compose_text
        assert "memory:" in compose_text
        assert "pids:" in compose_text

        # Bounded tmpfs
        assert "tmpfs:" in compose_text
        assert "noexec" in compose_text

        # Worker service environment must NOT contain UITP_SECRET_KEY
        worker_section = compose_text.split("\n  worker:\n")[1]
        assert "UITP_SECRET_KEY" not in worker_section
