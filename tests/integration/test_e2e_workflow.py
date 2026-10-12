"""
tests/integration/test_e2e_workflow.py
======================================
Complete End-to-End Integration and Milestone 1 Verification Suite (Task P1-06C).

Exercises the full 17-step workflow:
  1. Create and authenticate test user.
  2. Create a project.
  3. Verify project ownership and role permissions.
  4. Configure project environment pointing to local mock API fixture.
  5. Create declarative HTTP test plan.
  6. Validate that malformed or executable-code-like steps are rejected.
  7. Create a plan version and verify version history.
  8. Approve the exact plan version.
  9. Attempt to execute an unapproved plan/version and verify PLAN_NOT_APPROVED (422).
  10. Execute the approved plan against the mock fixture.
  11. Verify each step attempt and assertion result.
  12. Verify run status, timing, and timeline events.
  13. Verify evidence is stored with redaction and size limits.
  14. Retrieve artifact as authorized project member.
  15. Attempt retrieval from different project and verify 404 tenant isolation masking.
  16. Verify policy-blocked target is not contacted.
  17. Verify redirect handling cannot bypass the SSRF policy.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from tests.fixtures.mock_http_api import create_mock_api_app
from tests.fixtures.mock_http_api.app import MockApiRecorder


async def _register_and_login(client: AsyncClient, email: str, password: str = "correct-horse-battery-p106c") -> dict[str, Any]:
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "E2E Test User"},
    )
    assert reg_resp.status_code in (201, 200)
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200
    return login_resp.json()


@pytest.fixture(autouse=True)
def wire_mock_api(app):
    """Wire in-process mock HTTP API fixture and attach recorder to app state."""
    recorder = MockApiRecorder()
    mock_app = create_mock_api_app(recorder=recorder)
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    app.state.mock_recorder = recorder
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport
    if hasattr(app.state, "mock_recorder"):
        del app.state.mock_recorder


@pytest.mark.integration
class TestEndToEndMilestone1Workflow:
    @pytest.mark.asyncio
    async def test_complete_m1_http_workflow(self, client: AsyncClient, app) -> None:
        recorder: MockApiRecorder = app.state.mock_recorder

        # -------------------------------------------------------------------
        # Step 1: Create and authenticate test user
        # -------------------------------------------------------------------
        user_info = await _register_and_login(client, "lead_tester@uitp.local")
        user_id = user_info["user"]["id"]
        assert user_id is not None

        # -------------------------------------------------------------------
        # Step 2: Create a project
        # -------------------------------------------------------------------
        proj_resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "E2E Testing Project",
                "description": "Milestone 1 Verification Project",
            },
        )
        assert proj_resp.status_code == 201
        project = proj_resp.json()
        project_id = project["id"]
        assert project["name"] == "E2E Testing Project"

        # -------------------------------------------------------------------
        # Step 3: Verify project ownership and role permissions
        # -------------------------------------------------------------------
        get_proj_resp = await client.get(f"/api/v1/projects/{project_id}")
        assert get_proj_resp.status_code == 200
        assert get_proj_resp.json()["owner_id"] == user_id
        assert get_proj_resp.json()["status"] == "active"

        # -------------------------------------------------------------------
        # Step 4: Configure project environment pointing to mock fixture
        # -------------------------------------------------------------------
        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={
                "name": "Local Mock In-Process Env",
                "base_url": "http://mock-api.local",
                "allowlist": ["mock-api.local"],
                "secret_refs": {"MOCK_KEY": "vault://test/key"},
                "description": "Environment wired to ASGI mock fixture",
            },
        )
        assert env_resp.status_code == 201
        environment = env_resp.json()
        env_id = environment["id"]
        assert environment["base_url"] == "http://mock-api.local"
        assert "mock-api.local" in environment["allowlist"]

        # -------------------------------------------------------------------
        # Step 5: Create a declarative HTTP test plan (Version 1)
        # -------------------------------------------------------------------
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Deterministic Health Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "step_get_ok",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"path": "/ok"},
                        "assertions": [
                            {"type": "status_code", "value": 200},
                            {"type": "json_path_equals", "path": "$.status", "value": "ok"},
                        ],
                        "timeout_seconds": 10.0,
                        "retry_policy": {"max_attempts": 1},
                        "idempotency_class": "safe",
                        "risk_level": "low",
                    }
                ],
            },
        )
        assert plan_resp.status_code == 201
        plan_data = plan_resp.json()
        plan_id = plan_data["plan_id"]
        assert plan_data["version"] == 1
        assert plan_data["status"] == "draft"

        # -------------------------------------------------------------------
        # Step 6: Validate that malformed or executable-code-like steps are rejected
        # -------------------------------------------------------------------
        # 6a. Obvious code execution in action name
        bad_action_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Malicious Code Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "step_bad_action",
                        "adapter_type": "http_api",
                        "action": "exec('import os')",
                        "inputs": {"path": "/ok"},
                    }
                ],
            },
        )
        assert bad_action_resp.status_code == 422

        # 6b. Raw credential embedded in step inputs
        bad_creds_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Raw Credential Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "step_bad_creds",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"path": "/ok", "password": "plain-text-secret"},
                    }
                ],
            },
        )
        assert bad_creds_resp.status_code == 422

        # -------------------------------------------------------------------
        # Step 7: Create a plan version and verify version history
        # -------------------------------------------------------------------
        v2_resp = await client.post(
            f"/api/v1/plans/{plan_id}/versions",
            json={
                "steps": [
                    {
                        "step_id": "step_1_get_ok",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {
                            "path": "/ok",
                            "headers": {
                                "Authorization": "Bearer super-secret-e2e-token",
                                "X-Custom-Trace": "e2e-trace-id",
                            },
                        },
                        "assertions": [
                            {"type": "status_code", "value": 200},
                            {"type": "json_path_equals", "path": "$.status", "value": "ok"},
                        ],
                        "timeout_seconds": 10.0,
                        "retry_policy": {"max_attempts": 2, "backoff_seconds": 0.1},
                        "idempotency_class": "safe",
                    },
                    {
                        "step_id": "step_2_post_create",
                        "adapter_type": "http_api",
                        "action": "post",
                        "inputs": {
                            "path": "/create",
                            "json": {"item_name": "E2E Widget", "quantity": 10},
                        },
                        "assertions": [
                            {"type": "status_code", "value": 201},
                            {"type": "json_path_equals", "path": "$.status", "value": "created"},
                        ],
                        "timeout_seconds": 10.0,
                        "retry_policy": {"max_attempts": 1},
                        "idempotency_class": "non_idempotent",
                    },
                ],
            },
        )
        assert v2_resp.status_code == 201
        v2_data = v2_resp.json()
        assert v2_data["version"] == 2
        assert v2_data["status"] == "draft"

        # Check version listing
        versions_resp = await client.get(f"/api/v1/plans/{plan_id}/versions")
        assert versions_resp.status_code == 200
        version_list = versions_resp.json()["versions"]
        assert len(version_list) == 2
        versions_numbers = [v["version"] for v in version_list]
        assert 1 in versions_numbers and 2 in versions_numbers

        # -------------------------------------------------------------------
        # Step 8: Approve the exact plan version (Version 2)
        # -------------------------------------------------------------------
        appr_resp = await client.post(
            f"/api/v1/plans/{plan_id}/approve",
            json={"version": 2},
        )
        assert appr_resp.status_code == 200
        assert appr_resp.json()["status"] == "approved"
        assert appr_resp.json()["version"] == 2

        # -------------------------------------------------------------------
        # Step 9: Attempt to execute unapproved plan/version and verify 422
        # -------------------------------------------------------------------
        unapproved_exec_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert unapproved_exec_resp.status_code == 422
        assert "PLAN_NOT_APPROVED" in unapproved_exec_resp.text

        # -------------------------------------------------------------------
        # Step 10: Execute approved plan (Version 2) against mock fixture
        # -------------------------------------------------------------------
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 2},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        run_id = run_data["id"]
        assert run_data["plan_version"] == 2
        assert run_data["status"] == "passed"

        # -------------------------------------------------------------------
        # Step 11: Verify step attempts and assertion results
        # -------------------------------------------------------------------
        attempts = run_data["step_attempts"]
        assert len(attempts) == 2
        assert attempts[0]["step_id"] == "step_1_get_ok"
        assert attempts[0]["status"] == "passed"
        assert attempts[0]["started_at"] is not None
        assert attempts[0]["finished_at"] is not None

        assert attempts[1]["step_id"] == "step_2_post_create"
        assert attempts[1]["status"] == "passed"

        # Verify mock recorder actually observed both requests
        assert len(recorder.requests) >= 2
        recorded_paths = [r["path"] for r in recorder.requests]
        assert "/ok" in recorded_paths
        assert "/create" in recorded_paths

        # -------------------------------------------------------------------
        # Step 12: Verify run status, timing, and timeline events
        # -------------------------------------------------------------------
        events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) >= 5
        event_types = [e["event_type"] for e in events]
        assert "run_started" in event_types
        assert "step_started" in event_types
        assert "step_passed" in event_types
        assert "run_passed" in event_types

        # -------------------------------------------------------------------
        # Step 13: Verify that evidence is stored with redaction & size limits
        # -------------------------------------------------------------------
        artifacts_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        assert artifacts_resp.status_code == 200
        artifacts = artifacts_resp.json()
        assert len(artifacts) >= 2

        for art in artifacts:
            assert art["run_id"] == run_id
            assert art["type"] == "response_log"
            assert art["checksum"] is not None
            assert art["size_bytes"] > 0
            assert "retention_until" in art
            # Object key (physical path) must NEVER leak across the API
            assert "object_key" not in art

        first_artifact = artifacts[0]
        artifact_id = first_artifact["id"]

        # -------------------------------------------------------------------
        # Step 14: Retrieve artifact as authorized project member
        # -------------------------------------------------------------------
        art_get_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert art_get_resp.status_code == 200
        art_body = art_get_resp.json()
        assert art_body["artifact"]["id"] == artifact_id
        assert art_body["inline_content"] is not None

        # Verify base64 content decodes and shows Authorization header was REDACTED
        raw_evidence = base64.b64decode(art_body["inline_content"]).decode("utf-8")
        parsed_evidence = json.loads(raw_evidence)
        assert "super-secret-e2e-token" not in raw_evidence
        req_headers = parsed_evidence["request"]["headers"]
        assert req_headers.get("Authorization") == "[REDACTED]"
        assert req_headers.get("X-Custom-Trace") == "e2e-trace-id"

        # Verify raw binary download (?download=true)
        download_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}?download=true")
        assert download_resp.status_code == 200
        assert "attachment" in download_resp.headers.get("content-disposition", "")
        assert download_resp.content == raw_evidence.encode("utf-8")

        # -------------------------------------------------------------------
        # Step 15: Cross-project retrieval returns 404 tenant isolation masking
        # -------------------------------------------------------------------
        # User B setup (different user, different project)
        await _register_and_login(client, "outsider@other-company.local")
        other_proj_resp = await client.post("/api/v1/projects", json={"name": "Tenant B Project"})
        assert other_proj_resp.status_code == 201

        # User B attempts to access User A's run artifacts
        cross_list = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        assert cross_list.status_code == 404

        cross_get = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert cross_get.status_code == 404

        cross_run = await client.get(f"/api/v1/runs/{run_id}")
        assert cross_run.status_code == 404

        # Switch back to User A for security checks
        await _register_and_login(client, "lead_tester@uitp.local")

        # -------------------------------------------------------------------
        # Step 16: Verify that a policy-blocked target is not contacted
        # -------------------------------------------------------------------
        recorder.reset()
        blocked_plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "SSRF Blocked Target Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "step_blocked_target",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {
                            # Absolute target pointing to AWS metadata IP
                            "url": "http://169.254.169.254/latest/meta-data/",
                        },
                        "timeout_seconds": 5.0,
                    }
                ],
            },
        )
        blocked_plan_id = blocked_plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{blocked_plan_id}/approve", json={"version": 1})

        blocked_run_resp = await client.post(
            f"/api/v1/plans/{blocked_plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert blocked_run_resp.status_code == 201
        blocked_run = blocked_run_resp.json()
        assert blocked_run["status"] in ("failed", "blocked")
        assert blocked_run["step_attempts"][0]["status"] in ("failed", "blocked")
        assert blocked_run["step_attempts"][0]["error_code"] in ("POLICY_BLOCKED", "TARGET_NOT_ALLOWLISTED")
        # Ensure zero outbound requests occurred
        assert len(recorder.requests) == 0

        # -------------------------------------------------------------------
        # Step 17: Verify redirect handling cannot bypass the SSRF policy
        # -------------------------------------------------------------------
        recorder.reset()
        redirect_plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "SSRF Redirect Loop Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "step_private_redirect",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"path": "/private-redirect"},
                        "timeout_seconds": 5.0,
                    }
                ],
            },
        )
        redirect_plan_id = redirect_plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{redirect_plan_id}/approve", json={"version": 1})

        redirect_run_resp = await client.post(
            f"/api/v1/plans/{redirect_plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert redirect_run_resp.status_code == 201
        redirect_run = redirect_run_resp.json()
        assert redirect_run["status"] in ("failed", "blocked")
        assert redirect_run["step_attempts"][0]["status"] in ("failed", "blocked")
        # Step attempted /private-redirect (which was recorded by mock fixture),
        # but the redirected target http://169.254.169.254 was blocked and NEVER contacted!
        assert len(recorder.requests) == 1
        assert recorder.requests[0]["path"] == "/private-redirect"
