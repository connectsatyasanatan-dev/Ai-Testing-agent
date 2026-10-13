"""tests/integration/test_worker_leases_and_idempotency.py
======================================================
Integration tests for:
  - Idempotency-Key header deduplication (Task P3-03).
  - Worker lease heartbeat renewal and stale run recovery (Task P3-02).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_api.models.run import Run, RunEvent


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


async def _register_and_login(client: AsyncClient, email: str = "lease_user@example.com") -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secure-password-1234"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "secure-password-1234"},
    )
    assert resp.status_code == 200
    return resp.json()


async def _setup_project_env_and_plan(
    client: AsyncClient,
    project_name: str = "Lease Project",
) -> tuple[str, str, str]:
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Local Mock Env",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    env_id = env_resp.json()["id"]

    plan_resp = await client.post(
        f"/api/v1/projects/{project_id}/plans",
        json={
            "name": "Smoke Plan",
            "adapter_type": "http_api",
            "steps": [_valid_step_payload("step_1", "/ok")],
        },
    )
    plan_id = plan_resp.json()["plan_id"]

    await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
    return project_id, env_id, plan_id


@pytest.fixture(autouse=True)
def wire_mock_api_transport(app):
    mock_app = create_mock_api_app()
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport


@pytest.mark.integration
class TestIdempotencyAndWorkerLeaseRecovery:
    @pytest.mark.asyncio
    async def test_idempotency_key_header_deduplication(self, client: AsyncClient) -> None:
        """Calling run creation with Idempotency-Key header returns the exact same run."""
        await _register_and_login(client, "idem_user@example.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Idem Project")

        # First execution with Idempotency-Key header
        resp1 = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
            headers={"Idempotency-Key": "unique-idem-token-12345"},
        )
        assert resp1.status_code == 201
        run1 = resp1.json()

        # Second execution with exact same Idempotency-Key header
        resp2 = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
            headers={"Idempotency-Key": "unique-idem-token-12345"},
        )
        assert resp2.status_code == 201
        run2 = resp2.json()

        assert run1["id"] == run2["id"]
        assert run2["idempotency_key"] == "unique-idem-token-12345"

    @pytest.mark.asyncio
    async def test_worker_heartbeat_renews_lease(self, client: AsyncClient, db_session) -> None:
        """Heartbeat endpoint extends lease_expires_at for active worker."""
        await _register_and_login(client, "hb_user@example.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Heartbeat Project")

        # Manually create a running run
        run_id = "test-running-run-001"
        now = datetime.now(UTC)
        initial_lease = now + timedelta(seconds=30)
        run = Run(
            id=run_id,
            plan_id=plan_id,
            plan_version=1,
            environment_id=env_id,
            project_id=project_id,
            status="running",
            requested_by="u1",
            worker_id="worker-host-1",
            started_at=now,
            lease_expires_at=initial_lease,
        )
        db_session.add(run)
        await db_session.commit()

        # Renew lease via heartbeat
        hb_resp = await client.post(
            f"/api/v1/runs/{run_id}/heartbeat",
            json={"worker_id": "worker-host-1", "lease_seconds": 120},
        )
        assert hb_resp.status_code == 200

        # Verify DB lease updated
        await db_session.refresh(run)
        assert run.lease_expires_at is not None
        initial_ts = initial_lease.timestamp() if initial_lease.tzinfo else initial_lease.replace(tzinfo=UTC).timestamp()
        updated_ts = run.lease_expires_at.timestamp() if run.lease_expires_at.tzinfo else run.lease_expires_at.replace(tzinfo=UTC).timestamp()
        assert updated_ts > initial_ts

    @pytest.mark.asyncio
    async def test_recover_stale_runs_marks_failed(self, client: AsyncClient, db_session) -> None:
        """Runs with expired worker lease must be recovered and transitioned to failed."""
        await _register_and_login(client, "recover_user@example.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Recovery Project")

        # Create a crashed/orphaned run with expired lease
        crashed_run_id = "crashed-worker-run-999"
        past_time = datetime.now(UTC) - timedelta(minutes=10)
        run = Run(
            id=crashed_run_id,
            plan_id=plan_id,
            plan_version=1,
            environment_id=env_id,
            project_id=project_id,
            status="running",
            requested_by="u1",
            worker_id="dead-worker-pid-42",
            started_at=past_time,
            lease_expires_at=past_time + timedelta(seconds=60),  # expired 9 minutes ago
        )
        db_session.add(run)
        await db_session.commit()

        # Trigger recovery
        rec_resp = await client.post("/api/v1/runs/recover-stale?timeout_seconds=300")
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()
        assert crashed_run_id in rec_data["recovered_run_ids"]

        # Verify in DB: run status is failed, not passed, not running
        await db_session.refresh(run)
        assert run.status == "failed"
        assert run.finished_at is not None

        # Verify run_failed event was written
        evt_stmt = select(RunEvent).where(RunEvent.run_id == crashed_run_id, RunEvent.event_type == "run_failed")
        evt_res = await db_session.execute(evt_stmt)
        events = evt_res.scalars().all()
        assert len(events) == 1
        assert "Worker lease expired" in events[0].message
