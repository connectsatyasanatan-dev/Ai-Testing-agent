"""tests/integration/test_concurrency_and_quotas.py
================================================
Integration tests for Task P3-05:
  - Per-project concurrency limits and quotas.
  - HTTP 429 RATE_LIMIT_EXCEEDED when quota is exhausted.
  - Recovery after active runs finish.
  - Project isolation for concurrency limits.
"""
from __future__ import annotations

from unittest.mock import patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.run import Run
from uitp_contracts.enums import ErrorCode, RunStatus


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


async def _register_and_login(client: AsyncClient, email: str = "quota_user@example.com") -> dict:
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
    project_name: str = "Quota Project",
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


@pytest.mark.integration
class TestConcurrencyLimitsAndQuotas:
    @pytest.mark.asyncio
    async def test_concurrency_limit_blocks_excessive_runs(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Creating runs beyond the project concurrency quota returns 429."""
        await _register_and_login(client, email="concurrency_tester@example.com")
        project_id, env_id, plan_id = await _setup_project_env_and_plan(client, "Concurrency Proj")

        # Set limit to 2 active runs for this test
        with patch("uitp_api.services.run_service.MAX_CONCURRENT_RUNS_PER_PROJECT", 2):
            # 1. Insert 2 active (queued/running) runs directly into DB
            run1 = Run(
                id="active-run-1",
                project_id=project_id,
                plan_id=plan_id,
                plan_version=1,
                environment_id=env_id,
                status=RunStatus.RUNNING.value,
                requested_by="tester",
            )
            run2 = Run(
                id="active-run-2",
                project_id=project_id,
                plan_id=plan_id,
                plan_version=1,
                environment_id=env_id,
                status=RunStatus.QUEUED.value,
                requested_by="tester",
            )
            db_session.add_all([run1, run2])
            await db_session.commit()

            # 2. Attempt to create a 3rd run -> must be rejected with HTTP 429
            resp = await client.post(
                f"/api/v1/plans/{plan_id}/runs",
                json={
                    "plan_version": 1,
                    "environment_id": env_id,
                },
            )
            assert resp.status_code == 429
            error_data = resp.json()["error"]
            assert error_data["code"] == ErrorCode.RATE_LIMIT_EXCEEDED
            assert "concurrency limit" in error_data["message"].lower()
            assert "Retry-After" in resp.headers

            # 3. Complete run1 -> active count drops to 1
            run1.status = RunStatus.PASSED.value
            await db_session.commit()

            # 4. Now attempt to create a run -> should proceed past concurrency check
            # (Note: In sync mode it executes steps)
            with patch("uitp_api.services.run_service.HttpApiAdapter.execute_step") as mock_step:
                from uitp_adapter_base.base import StepExecutionResult
                mock_step.return_value = StepExecutionResult(status=RunStatus.PASSED, duration_ms=10)
                resp2 = await client.post(
                    f"/api/v1/plans/{plan_id}/runs",
                    json={
                        "plan_version": 1,
                        "environment_id": env_id,
                    },
                )
                assert resp2.status_code == 201

    @pytest.mark.asyncio
    async def test_concurrency_limit_project_isolation(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Active runs in Project A do not affect the quota of Project B."""
        await _register_and_login(client, email="isolated_tester@example.com")
        proj_a, env_a, plan_a = await _setup_project_env_and_plan(client, "Project Alpha")
        proj_b, env_b, plan_b = await _setup_project_env_and_plan(client, "Project Beta")

        with patch("uitp_api.services.run_service.MAX_CONCURRENT_RUNS_PER_PROJECT", 1):
            # Exhaust quota on Project Alpha
            run_a = Run(
                id="active-alpha-run",
                project_id=proj_a,
                plan_id=plan_a,
                plan_version=1,
                environment_id=env_a,
                status=RunStatus.RUNNING.value,
                requested_by="tester",
            )
            db_session.add(run_a)
            await db_session.commit()

            # Alpha is blocked
            resp_alpha = await client.post(
                f"/api/v1/plans/{plan_a}/runs",
                json={"plan_version": 1, "environment_id": env_a},
            )
            assert resp_alpha.status_code == 429

            # Beta is NOT blocked
            with patch("uitp_api.services.run_service.HttpApiAdapter.execute_step") as mock_step:
                from uitp_adapter_base.base import StepExecutionResult
                mock_step.return_value = StepExecutionResult(status=RunStatus.PASSED, duration_ms=10)
                resp_beta = await client.post(
                    f"/api/v1/plans/{plan_b}/runs",
                    json={"plan_version": 1, "environment_id": env_b},
                )
                assert resp_beta.status_code == 201
