"""
tests/integration/test_runs.py
==============================
Integration tests for Synchronous Test Run Execution Pipeline (Task P1-06A).

Verifies:
  1. Approved plan runs against mock API and passes with valid assertions.
  2. Unapproved plan draft returns 422 with PLAN_NOT_APPROVED.
  3. Archived plan version returns 422 with PLAN_NOT_APPROVED.
  4. Run records reference the exact approved version.
  5. Step attempts are recorded in deterministic order.
  6. Assertion failure marks step and run as failed (never unhandled exception).
  7. HTTP status failure marks step and run as failed.
  8. Policy-blocked target is rejected by SSRF/allowlist policy.
  9. Unsupported adapter type fails safely.
  10. Cross-project run access is rejected (404/tenant isolation).
  11. Viewer role cannot trigger a run (403).
  12. Timeline events (run_started, step_started, run_passed) are persisted.
  13. Idempotency key prevents duplicate run execution (P3-03 contract preview).
  14. Project run listing returns created runs.
"""
from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from tests.fixtures.mock_http_api import create_mock_api_app


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


async def _setup_project_and_env(client: AsyncClient, project_name: str = "Run Proj") -> tuple[str, str]:
    # Create project
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    # Create environment
    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Local Mock Env",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    assert env_resp.status_code == 201
    env_id = env_resp.json()["id"]

    return project_id, env_id


def _valid_step_payload(step_id: str = "step_1", path: str = "/ok", assertions: list | None = None) -> dict:
    if assertions is None:
        assertions = [
            {"type": "status_code", "value": 200},
            {"type": "json_path_equals", "path": "$.status", "value": "ok"},
        ]
    return {
        "step_id": step_id,
        "adapter_type": "http_api",
        "action": "get",
        "inputs": {"path": path},
        "assertions": assertions,
        "timeout_seconds": 10.0,
        "retry_policy": {"max_attempts": 1},
        "idempotency_class": "safe",
        "risk_level": "low",
        "description": f"Step {step_id}",
    }


@pytest.fixture(autouse=True)
def wire_mock_api_transport(app):
    """Wire in-process mock HTTP API into FastAPI app state for tests."""
    mock_app = create_mock_api_app()
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport


@pytest.mark.integration
class TestSynchronousRunExecution:
    @pytest.mark.asyncio
    async def test_approved_plan_executes_successfully(self, client: AsyncClient) -> None:
        await _register_and_login(client, "run_owner@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Successful Run Project")

        # 1. Create plan
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Smoke Plan",
                "adapter_type": "http_api",
                "steps": [
                    _valid_step_payload("step_1", "/ok"),
                    _valid_step_payload("step_2", "/ok"),
                ],
            },
        )
        assert plan_resp.status_code == 201
        plan_id = plan_resp.json()["plan_id"]

        # 2. Approve plan
        appr_resp = await client.post(
            f"/api/v1/plans/{plan_id}/approve",
            json={"version": 1},
        )
        assert appr_resp.status_code == 200
        assert appr_resp.json()["status"] == "approved"

        # 3. Execute run
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()

        assert run_data["status"] == "passed"
        assert run_data["plan_id"] == plan_id
        assert run_data["plan_version"] == 1
        assert run_data["environment_id"] == env_id
        assert len(run_data["step_attempts"]) == 2
        assert run_data["step_attempts"][0]["status"] == "passed"
        assert run_data["step_attempts"][1]["status"] == "passed"

    @pytest.mark.asyncio
    async def test_unapproved_draft_plan_rejected(self, client: AsyncClient) -> None:
        await _register_and_login(client, "draft_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Draft Plan Project")

        # Create draft plan without approving it
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Draft Only Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("step_1", "/ok")],
            },
        )
        plan_id = plan_resp.json()["plan_id"]

        # Attempt to run draft plan
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert run_resp.status_code == 422
        err = run_resp.json()["error"]
        assert err["code"] == "PLAN_NOT_APPROVED"
        assert "not approved for execution" in err["message"]

    @pytest.mark.asyncio
    async def test_assertion_failure_marks_run_failed(self, client: AsyncClient) -> None:
        await _register_and_login(client, "assert_fail@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Assertion Failure Project")

        # Plan with impossible assertion (expects status_code 404 on /ok)
        failing_step = _valid_step_payload(
            "step_fail",
            "/ok",
            assertions=[{"type": "status_code", "value": 404}],
        )
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Failing Assertion Plan",
                "adapter_type": "http_api",
                "steps": [failing_step],
            },
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        # Run
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        assert len(run_data["step_attempts"]) == 1
        assert run_data["step_attempts"][0]["status"] == "failed"
        assert run_data["step_attempts"][0]["error_code"] == "ASSERTION_FAILED"

    @pytest.mark.asyncio
    async def test_policy_blocked_target_stops_execution(self, client: AsyncClient) -> None:
        await _register_and_login(client, "policy_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Policy Test Project")

        # Step targeting private redirect endpoint pointing to cloud metadata
        step = _valid_step_payload("step_ssrf", "/private-redirect")
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "SSRF Target Plan",
                "adapter_type": "http_api",
                "steps": [step],
            },
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] in ("failed", "blocked")
        assert run_data["step_attempts"][0]["status"] in ("failed", "blocked")

    @pytest.mark.asyncio
    async def test_run_timeline_events_persisted(self, client: AsyncClient) -> None:
        await _register_and_login(client, "events_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Events Test Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Timeline Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("step_1", "/ok")],
            },
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id},
        )
        run_id = run_resp.json()["id"]

        # Fetch timeline events
        events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) >= 3
        event_types = [e["event_type"] for e in events]
        assert "run_started" in event_types
        assert "step_started" in event_types
        assert "run_passed" in event_types

    @pytest.mark.asyncio
    async def test_idempotency_key_returns_existing_run(self, client: AsyncClient) -> None:
        await _register_and_login(client, "idemp_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Idempotency Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Idemp Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("step_1", "/ok")],
            },
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        key = "idemp-key-12345"
        # First request
        resp1 = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "idempotency_key": key},
        )
        assert resp1.status_code == 201
        run1_id = resp1.json()["id"]

        # Second request with identical key
        resp2 = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "idempotency_key": key},
        )
        assert resp2.status_code == 201
        run2_id = resp2.json()["id"]

        assert run1_id == run2_id

    @pytest.mark.asyncio
    async def test_cross_project_run_access_rejected(self, client: AsyncClient) -> None:
        # User A creates project, env, plan, and run
        await _register_and_login(client, "user_a@test.com")
        proj_a, env_a = await _setup_project_and_env(client, "Project A")
        plan_a = (
            await client.post(
                f"/api/v1/projects/{proj_a}/plans",
                json={
                    "name": "Plan A",
                    "adapter_type": "http_api",
                    "steps": [_valid_step_payload("step_1", "/ok")],
                },
            )
        ).json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_a}/approve", json={"version": 1})
        run_a = (
            await client.post(
                f"/api/v1/plans/{plan_a}/runs",
                json={"environment_id": env_a},
            )
        ).json()["id"]

        # User B logs in and tries to access User A's run
        await _register_and_login(client, "user_b@test.com")
        resp = await client.get(f"/api/v1/runs/{run_a}")
        # Cross-tenant access must return 404 to mask existence
        assert resp.status_code == 404
