"""
tests.integration.test_background_runs
======================================
Integration tests for durable background test execution with Redis/RQ:
  - Enqueue success and state tracking.
  - Partial failure resilience: transport error marks run failed, never leaving it hanging in queued.
  - Unauthorized and unapproved execution gating at API boundary.
  - End-to-end background flow: enqueue -> claim -> execute -> persist passed.
  - Idempotency key deduplication.
"""
from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_contracts.enums import RunStatus
from uitp_orchestrator.exceptions import QueueError, QueueUnavailableError
from uitp_orchestrator.queue import RunQueue
from uitp_worker_runner.execution import async_execute_run_job


def _valid_step_payload(step_id: str = "step_1", path: str = "/ok") -> dict:
    return {
        "step_id": step_id,
        "adapter_type": "http_api",
        "action": "get",
        "inputs": {"path": path},
        "assertions": [{"type": "status_code", "value": 200}],
        "timeout_seconds": 10.0,
        "retry_policy": {"max_attempts": 1, "backoff_seconds": 0.1},
        "idempotency_class": "safe",
        "risk_level": "low",
        "description": f"Step {step_id}",
    }


async def _register_and_login(client: AsyncClient, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery"},
    )
    return resp.json()


async def _setup_project_env_and_plan(
    client: AsyncClient,
    project_name: str = "Bg Proj",
    approved: bool = True,
) -> tuple[str, str, str]:
    # Project
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    project_id = proj_resp.json()["id"]

    # Environment
    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Local Mock Env",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    env_id = env_resp.json()["id"]

    # Plan
    plan_resp = await client.post(
        f"/api/v1/projects/{project_id}/plans",
        json={
            "name": "Smoke Plan",
            "adapter_type": "http_api",
            "steps": [_valid_step_payload("step_1", "/ok")],
        },
    )
    plan_id = plan_resp.json()["plan_id"]

    if approved:
        await client.post(
            f"/api/v1/plans/{plan_id}/approve",
            json={"version": 1},
        )

    return project_id, env_id, plan_id


@pytest.fixture(autouse=True)
def wire_mock_api_transport(app):
    mock_app = create_mock_api_app()
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    app.state.run_queue = RunQueue(is_mock=True)
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport
    if hasattr(app.state, "run_queue"):
        del app.state.run_queue


@pytest.mark.integration
class TestBackgroundRunExecution:
    @pytest.mark.asyncio
    async def test_enqueue_run_creates_queued_record(self, client: AsyncClient) -> None:
        """POST with background=True must return 201 with status=queued."""
        await _register_and_login(client, "bg_user@test.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Enqueue Project")

        resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "queued"
        run_id = data["id"]

        # Verify timeline events contain run_queued
        events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
        assert events_resp.status_code == 200
        event_types = [e["event_type"] for e in events_resp.json()]
        assert "run_queued" in event_types

    @pytest.mark.asyncio
    async def test_enqueue_partial_failure_resilience(self, client: AsyncClient, app, db_session) -> None:
        """If queue transport fails, run record must transition to failed (never stuck in queued)."""
        await _register_and_login(client, "fail_user@test.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Resilience Project")

        # Configure broken queue on app state
        class BrokenQueue(RunQueue):
            def enqueue_run(self, *args, **kwargs):
                raise QueueUnavailableError("Redis connection dropped during enqueue")

        app.state.run_queue = BrokenQueue(is_mock=True)

        resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert resp.status_code == 503

        # But check DB: the run record must be in 'failed' state and have a run_failed event
        from uitp_api.models.run import Run, RunEvent
        from sqlalchemy import select

        stmt = select(Run).where(Run.plan_id == plan_id)
        result = await db_session.execute(stmt)
        runs = result.scalars().all()
        assert len(runs) == 1
        failed_run = runs[0]
        assert failed_run.status == "failed"
        assert failed_run.finished_at is not None

        # Verify run_failed event was stored
        evt_stmt = select(RunEvent).where(RunEvent.run_id == failed_run.id, RunEvent.event_type == "run_failed")
        evt_res = await db_session.execute(evt_stmt)
        fail_evts = evt_res.scalars().all()
        assert len(fail_evts) == 1
        assert "Queue transport failure" in fail_evts[0].message

    @pytest.mark.asyncio
    async def test_unapproved_plan_rejected_at_api_boundary(self, client: AsyncClient) -> None:
        """Draft plan cannot be enqueued."""
        await _register_and_login(client, "draft_user@test.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Draft Enqueue", approved=False)

        resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PLAN_NOT_APPROVED"

    @pytest.mark.asyncio
    async def test_idempotency_key_deduplication(self, client: AsyncClient) -> None:
        """Duplicate requests with same idempotency key return original run."""
        await _register_and_login(client, "idem_user@test.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Idempotency Project")

        key = "idemp-bg-test-123"
        resp1 = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1, "idempotency_key": key},
        )
        assert resp1.status_code == 201
        run1_id = resp1.json()["id"]

        resp2 = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1, "idempotency_key": key},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == run1_id

    @pytest.mark.asyncio
    async def test_end_to_end_background_execution_flow(self, client: AsyncClient, db_session) -> None:
        """Full flow: API enqueues run -> worker executes run -> run passes."""
        await _register_and_login(client, "e2e_worker_user@test.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "E2E Worker Project")

        # 1. API Enqueue
        resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs?background=true",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert resp.status_code == 201
        run_id = resp.json()["id"]
        assert resp.json()["status"] == "queued"

        # 2. Worker executes job
        mock_transport = httpx.ASGITransport(app=create_mock_api_app())
        worker_result = await async_execute_run_job(
            run_id=run_id,
            worker_id="background-worker-1",
            session=db_session,
            transport=mock_transport,
        )
        assert worker_result["status"] == "passed"

        # 3. Retrieve run via API
        run_resp = await client.get(f"/api/v1/runs/{run_id}")
        assert run_resp.status_code == 200
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        assert run_data["worker_id"] == "background-worker-1"
        assert run_data["started_at"] is not None
        assert run_data["finished_at"] is not None
        assert len(run_data["step_attempts"]) == 1
        assert run_data["step_attempts"][0]["status"] == "passed"

        # 4. Verify full timeline events
        events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
        assert events_resp.status_code == 200
        event_types = [e["event_type"] for e in events_resp.json()]
        assert "run_queued" in event_types
        assert "run_started" in event_types
        assert "step_started" in event_types
        assert "step_passed" in event_types
        assert "run_passed" in event_types
