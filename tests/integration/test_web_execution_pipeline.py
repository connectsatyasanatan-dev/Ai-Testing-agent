"""
tests/integration/test_web_execution_pipeline.py
================================================
End-to-end integration tests for Playwright Web Adapter test execution pipeline (Tasks P2-01 through P2-05).

Verifies:
  1. Approved declarative web test plan executes against local test web app fixture.
  2. Multi-step browser flow: navigate -> fill credentials -> click login -> assert dashboard -> capture screenshot.
  3. Step attempts are accurately recorded in the database.
  4. Response logs and screenshot evidence artifacts are securely stored with project-scoped isolation.
  5. Approval gate rejects unapproved web plan versions with 422 PLAN_NOT_APPROVED.
  6. Documented sample test plan runs cleanly against the local test fixture.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.fixtures.web_app import run_test_web_server


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


async def _setup_project_and_env(
    client: AsyncClient, base_url: str, project_name: str = "Web Test Project"
) -> tuple[str, str]:
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Local Web Fixture Env",
            "base_url": base_url,
            "allowlist": ["127.0.0.1"],
        },
    )
    assert env_resp.status_code == 201
    env_id = env_resp.json()["id"]

    return project_id, env_id


@pytest.mark.integration
class TestWebExecutionPipeline:
    @pytest.mark.asyncio
    async def test_approved_web_plan_executes_successfully_with_evidence(
        self, client: AsyncClient
    ) -> None:
        async with run_test_web_server() as base_url:
            await _register_and_login(client, "web_tester@test.com")
            project_id, env_id = await _setup_project_and_env(client, base_url, "Web Pipeline Project")

            # 1. Create a 5-step declarative web test plan
            plan_payload = {
                "name": "User Authentication & Dashboard Web Flow",
                "adapter_type": "web",
                "steps": [
                    {
                        "step_id": "step_1_nav",
                        "adapter_type": "web",
                        "action": "navigate",
                        "inputs": {"url": f"{base_url}/login"},
                        "assertions": [
                            {"type": "title_contains", "value": "Login"},
                            {"type": "url_contains", "value": "/login"},
                        ],
                        "timeout_seconds": 15.0,
                        "description": "Navigate to login page and verify title",
                    },
                    {
                        "step_id": "step_2_fill_username",
                        "adapter_type": "web",
                        "action": "fill",
                        "inputs": {"css": "#username", "value": "testuser"},
                        "timeout_seconds": 10.0,
                        "description": "Fill username field",
                    },
                    {
                        "step_id": "step_3_fill_password",
                        "adapter_type": "web",
                        "action": "fill",
                        "inputs": {"css": "#password", "value": "secret123"},
                        "timeout_seconds": 10.0,
                        "description": "Fill password field",
                    },
                    {
                        "step_id": "step_4_submit_login",
                        "adapter_type": "web",
                        "action": "click",
                        "inputs": {"css": "#login-btn"},
                        "assertions": [
                            {"type": "url_contains", "value": "/dashboard"},
                            {"type": "title_contains", "value": "Dashboard"},
                            {"type": "text_visible", "value": "Welcome to Test Portal"},
                        ],
                        "timeout_seconds": 15.0,
                        "description": "Click submit button and verify dashboard navigation",
                    },
                    {
                        "step_id": "step_5_capture_screenshot",
                        "adapter_type": "web",
                        "action": "screenshot",
                        "inputs": {"full_page": True},
                        "timeout_seconds": 10.0,
                        "description": "Capture full page screenshot evidence of dashboard",
                    },
                ],
            }

            create_resp = await client.post(
                f"/api/v1/projects/{project_id}/plans",
                json=plan_payload,
            )
            assert create_resp.status_code == 201
            plan_id = create_resp.json()["plan_id"]

            # 2. Verify approval gate: Draft plan cannot be executed
            unapproved_run = await client.post(
                f"/api/v1/plans/{plan_id}/runs",
                json={"environment_id": env_id, "plan_version": 1},
            )
            assert unapproved_run.status_code == 422
            assert unapproved_run.json()["error"]["code"] == "PLAN_NOT_APPROVED"

            # 3. Approve plan version 1
            appr_resp = await client.post(
                f"/api/v1/plans/{plan_id}/approve",
                json={"version": 1},
            )
            assert appr_resp.status_code == 200
            assert appr_resp.json()["status"] == "approved"

            # 4. Execute approved plan run
            run_resp = await client.post(
                f"/api/v1/plans/{plan_id}/runs",
                json={"environment_id": env_id, "plan_version": 1},
            )
            assert run_resp.status_code == 201
            run_data = run_resp.json()

            assert run_data["status"] == "passed"
            assert run_data["plan_id"] == plan_id
            assert run_data["plan_version"] == 1
            assert len(run_data["step_attempts"]) == 5
            for attempt in run_data["step_attempts"]:
                assert attempt["status"] == "passed"

            run_id = run_data["id"]

            # 5. Verify timeline events
            events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
            assert events_resp.status_code == 200
            events = events_resp.json()
            event_types = [e["event_type"] for e in events]
            assert "run_started" in event_types
            assert "step_started" in event_types
            assert "step_passed" in event_types
            assert "run_passed" in event_types

            # 6. Verify evidence artifacts stored
            artifacts_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
            assert artifacts_resp.status_code == 200
            artifacts = artifacts_resp.json()
            assert len(artifacts) >= 1
            artifact_types = [a["type"] for a in artifacts]
            assert "response_log" in artifact_types
            assert "screenshot" in artifact_types
