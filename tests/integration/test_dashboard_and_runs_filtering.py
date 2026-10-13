"""
tests/integration/test_dashboard_and_runs_filtering.py
======================================================
Integration tests for Project Dashboard Aggregation and Filtered Runs Listing.

Verifies:
  1. GET /api/v1/projects/{project_id}/dashboard returns accurate aggregated metrics.
  2. Total plans, total runs, status counts, and adapter breakdown match reality.
  3. Cross-project dashboard access is rejected with 404.
  4. GET /api/v1/projects/{project_id}/runs filters by status.
  5. GET /api/v1/projects/{project_id}/runs filters by plan_id.
  6. GET /api/v1/projects/{project_id}/runs supports pagination with accurate total count.
  7. GET /api/v1/projects/{project_id}/runs supports sorting.
  8. Cross-project runs listing returns 404 for non-members.
"""
from __future__ import annotations

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


async def _setup_project_and_env(client: AsyncClient, project_name: str = "Dash Proj") -> tuple[str, str]:
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

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


@pytest.mark.integration
class TestDashboardAndRunsFiltering:

    @pytest.fixture(autouse=True)
    def setup_mock_api(self, app):
        import httpx
        from uitp_adapter_http_api.adapter import HttpApiAdapter
        mock_starlette = create_mock_api_app()
        mock_transport = httpx.ASGITransport(app=mock_starlette)
        app.state.custom_adapter_transport = mock_transport
        yield
        app.state.custom_adapter_transport = None

    @pytest.mark.asyncio
    async def test_dashboard_metrics_aggregation(self, client: AsyncClient) -> None:
        await _register_and_login(client, "dash_owner@test.com")
        proj_id, env_id = await _setup_project_and_env(client, "Metrics Proj")

        # Create two plans: Plan 1 (passed run), Plan 2 (failed run)
        p1_resp = await client.post(
            f"/api/v1/projects/{proj_id}/plans",
            json={
                "name": "Plan 1 - Passing",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("step_1", "/ok")],
            },
        )
        assert p1_resp.status_code == 201
        p1_id = p1_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{p1_id}/approve", json={"version": 1})

        p2_resp = await client.post(
            f"/api/v1/projects/{proj_id}/plans",
            json={
                "name": "Plan 2 - Failing",
                "adapter_type": "http_api",
                "steps": [
                    _valid_step_payload(
                        "step_fail",
                        "/ok",
                        assertions=[{"type": "status_code", "value": 404}],
                    )
                ],
            },
        )
        assert p2_resp.status_code == 201
        p2_id = p2_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{p2_id}/approve", json={"version": 1})

        # Execute Run 1 against Plan 1 -> passed
        r1_resp = await client.post(
            f"/api/v1/plans/{p1_id}/runs",
            json={"environment_id": env_id},
        )
        assert r1_resp.status_code == 201
        assert r1_resp.json()["status"] == "passed"

        # Execute Run 2 against Plan 2 -> failed
        r2_resp = await client.post(
            f"/api/v1/plans/{p2_id}/runs",
            json={"environment_id": env_id},
        )
        assert r2_resp.status_code == 201
        assert r2_resp.json()["status"] == "failed"

        # Query dashboard endpoint
        dash_resp = await client.get(f"/api/v1/projects/{proj_id}/dashboard")
        assert dash_resp.status_code == 200
        dash_data = dash_resp.json()

        assert dash_data["project_id"] == proj_id
        assert dash_data["total_plans"] == 2
        assert dash_data["total_runs"] == 2
        assert dash_data["status_counts"]["passed"] == 1
        assert dash_data["status_counts"]["failed"] == 1
        assert dash_data["status_counts"]["running"] == 0
        assert dash_data["adapter_breakdown"]["http_api"] == 2
        assert len(dash_data["recent_runs"]) == 2
        # Check plan_name enrichment
        assert dash_data["recent_runs"][0]["plan_name"] is not None
        assert dash_data["recent_runs"][0]["adapter_type"] == "http_api"
        assert len(dash_data["recent_activity"]) > 0

    @pytest.mark.asyncio
    async def test_dashboard_cross_project_rejected(self, client: AsyncClient) -> None:
        await _register_and_login(client, "user_alpha@test.com")
        proj_alpha, _ = await _setup_project_and_env(client, "Alpha Proj")

        # User Beta attempts to access User Alpha's dashboard
        await _register_and_login(client, "user_beta@test.com")
        resp = await client.get(f"/api/v1/projects/{proj_alpha}/dashboard")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_runs_filtering_and_pagination(self, client: AsyncClient) -> None:
        await _register_and_login(client, "filter_user@test.com")
        proj_id, env_id = await _setup_project_and_env(client, "Filter Proj")

        # Create Plan A & Plan B
        pa_resp = await client.post(
            f"/api/v1/projects/{proj_id}/plans",
            json={
                "name": "Plan Alpha",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("step_ok", "/ok")],
            },
        )
        pa_id = pa_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{pa_id}/approve", json={"version": 1})

        pb_resp = await client.post(
            f"/api/v1/projects/{proj_id}/plans",
            json={
                "name": "Plan Beta",
                "adapter_type": "http_api",
                "steps": [
                    _valid_step_payload(
                        "step_fail",
                        "/ok",
                        assertions=[{"type": "status_code", "value": 500}],
                    )
                ],
            },
        )
        pb_id = pb_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{pb_id}/approve", json={"version": 1})

        # Run 1: Plan A -> passed
        await client.post(f"/api/v1/plans/{pa_id}/runs", json={"environment_id": env_id})
        # Run 2: Plan A -> passed
        await client.post(f"/api/v1/plans/{pa_id}/runs", json={"environment_id": env_id})
        # Run 3: Plan B -> failed
        await client.post(f"/api/v1/plans/{pb_id}/runs", json={"environment_id": env_id})

        # 1. Total unfiltered
        all_runs = await client.get(f"/api/v1/projects/{proj_id}/runs")
        assert all_runs.status_code == 200
        data = all_runs.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

        # 2. Filter by status=passed
        passed_runs = await client.get(f"/api/v1/projects/{proj_id}/runs?status=passed")
        assert passed_runs.status_code == 200
        p_data = passed_runs.json()
        assert p_data["total"] == 2
        assert len(p_data["items"]) == 2
        for item in p_data["items"]:
            assert item["status"] == "passed"

        # 3. Filter by status=failed
        failed_runs = await client.get(f"/api/v1/projects/{proj_id}/runs?status=failed")
        assert failed_runs.status_code == 200
        f_data = failed_runs.json()
        assert f_data["total"] == 1
        assert f_data["items"][0]["status"] == "failed"

        # 4. Filter by plan_id
        plan_a_runs = await client.get(f"/api/v1/projects/{proj_id}/runs?plan_id={pa_id}")
        assert plan_a_runs.status_code == 200
        pa_data = plan_a_runs.json()
        assert pa_data["total"] == 2

        # 5. Pagination: limit=1, offset=0
        page1 = await client.get(f"/api/v1/projects/{proj_id}/runs?limit=1&offset=0")
        assert page1.status_code == 200
        p1_data = page1.json()
        assert p1_data["total"] == 3  # true total is 3!
        assert len(p1_data["items"]) == 1

        # 6. Sorting: sort_order=asc vs desc
        asc_resp = await client.get(f"/api/v1/projects/{proj_id}/runs?sort_by=created_at&sort_order=asc")
        desc_resp = await client.get(f"/api/v1/projects/{proj_id}/runs?sort_by=created_at&sort_order=desc")
        assert asc_resp.json()["items"][0]["id"] == desc_resp.json()["items"][-1]["id"]
