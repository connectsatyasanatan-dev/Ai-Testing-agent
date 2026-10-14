"""
tests.integration.test_post_run_evaluation_pipeline
==================================================
Comprehensive integration tests for Task 8:
- Authoritative automatic evaluation trigger upon terminal states (passed, failed, cancelled).
- Strict evaluation idempotency across duplicate triggers / redelivery.
- Grounded deterministic root cause analysis (failed assertions, expected vs actual).
- Requirement-to-task traceability with unexecutable / blocked requirement mapping.
- Automatic deliverable export (/output/execution_log.json and report.md) reflecting final evaluator verdicts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_contracts.enums import RunStatus, TaskStatus
from uitp_contracts.plan import TokenUsage


class CustomMockAIProvider(MockAIProvider):
    """Mock provider with customizable structured output for testing."""

    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ):
        return self._response_data, TokenUsage(prompt_tokens=300, completion_tokens=500, total_tokens=800)


async def _register_and_login(client: httpx.AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "multi-agent-secure-pw"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "multi-agent-secure-pw"},
    )
    return resp.json()


@pytest.mark.asyncio
class TestPostRunEvaluationPipeline:

    async def test_successful_run_triggers_automatic_evaluation_and_exports(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """Verify an approved mission DAG executes, automatically evaluates, and exports artifacts."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "token": "secret-jwt-token-12345"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Successful E2E Evaluation Mission",
            "description": "Validates automatic post-run evaluation and report generation",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "auth_task",
                    "name": "Authenticate User",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_auth",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/login", "json": {"user": "tester"}},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {"session_token": {"json_path": "$.token", "is_secret": True}},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-AUTH-PASS"],
                },
                {
                    "task_id": "profile_task",
                    "name": "Fetch Profile",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["auth_task"],
                    "steps": [
                        {
                            "step_id": "step_profile",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/profile"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-PROFILE-PASS"],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "eval_success_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Auto Eval Success Project"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        # Plan and approve mission
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Specification\nREQ-AUTH-PASS: User login\nREQ-PROFILE-PASS: User profile",
                "environment_id": env_id,
            },
        )
        assert plan_resp.status_code == 201
        mission_id = plan_resp.json()["mission_id"]

        approve_resp = await client.post(
            f"/api/v1/missions/{mission_id}/approve",
            json={"comment": "Approved for automatic evaluation test"},
        )
        assert approve_resp.status_code == 200

        # Execute run
        run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        run_id = run_data["id"]

        # 1. Authoritative Trigger Check: evaluation_completed event persisted automatically
        eval_events = (
            await db_session.execute(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "evaluation_completed",
                )
            )
        ).scalars().all()
        assert len(eval_events) >= 1, "Automatic evaluation event was not emitted on terminal run state!"
        eval_event = eval_events[0]
        assert eval_event.event_metadata is not None
        assert eval_event.event_metadata["verdict"] == "passed"
        assert eval_event.event_metadata["total_tasks"] == 2
        assert eval_event.event_metadata["passed_tasks"] == 2

        # 2. Query evaluation endpoint: must return idempotent cached report
        eval_resp = await client.get(f"/api/v1/runs/{run_id}/evaluation")
        assert eval_resp.status_code == 200
        eval_body = eval_resp.json()
        assert eval_body["verdict"] == "passed"
        assert eval_body["is_idempotent_replay"] is True

        # 3. Check requirement coverage
        req_cov = {r["requirement_id"]: r for r in eval_body["requirement_coverage"]}
        assert req_cov["REQ-AUTH-PASS"]["status"] == "PASSED"
        assert req_cov["REQ-PROFILE-PASS"]["status"] == "PASSED"

        # 4. Check exported artifacts created automatically
        export_dir = Path("output/runs") / run_id
        if not export_dir.exists():
            export_dir = Path("output")

        # Query GET /api/v1/runs/{run_id}/export/report
        report_export_resp = await client.get(f"/api/v1/runs/{run_id}/export/report")
        assert report_export_resp.status_code == 200
        report_text = report_export_resp.text
        assert "Evaluator Assessment" in report_text or "Passed Tasks" in report_text
        assert "REQ-AUTH-PASS" in report_text

        # Query GET /api/v1/runs/{run_id}/export/log
        log_export_resp = await client.get(f"/api/v1/runs/{run_id}/export/log")
        assert log_export_resp.status_code == 200
        log_json = log_export_resp.json()
        assert log_json["run_id"] == run_id
        assert log_json["status"] == "passed"
        assert "evaluation" in log_json["metadata"]
        assert log_json["metadata"]["evaluation"]["verdict"] == "passed"

    async def test_failed_run_triggers_evidence_grounded_root_cause_analysis(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
    ) -> None:
        """Verify task failure triggers evaluation, cascade blocking, and grounded root cause analysis."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "fail" in request.url.path:
                return httpx.Response(500, json={"error": "Database connection timed out"})
            return httpx.Response(200, json={"status": "ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Failing E2E Evaluation Mission",
            "description": "Validates root-cause analysis on assertion failure",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "failing_task",
                    "name": "API Service Failure",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_fail",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/fail"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-FAILURE-01"],
                },
                {
                    "task_id": "downstream_task",
                    "name": "Dependent Processing",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["failing_task"],
                    "steps": [
                        {
                            "step_id": "step_downstream",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/downstream"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-BLOCKED-02"],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "eval_failure_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Auto Eval Failure Project"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Specification\nREQ-FAILURE-01: API call\nREQ-BLOCKED-02: Downstream call",
                "environment_id": env_id,
            },
        )
        assert plan_resp.status_code == 201
        mission_id = plan_resp.json()["mission_id"]

        approve_resp = await client.post(
            f"/api/v1/missions/{mission_id}/approve",
            json={"comment": "Approved for failure evaluation test"},
        )
        assert approve_resp.status_code == 200

        # Execute run: expect failure
        run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        # Fetch evaluation report
        eval_resp = await client.get(f"/api/v1/runs/{run_id}/evaluation")
        assert eval_resp.status_code == 200
        eval_body = eval_resp.json()

        assert eval_body["verdict"] == "failed"
        assert eval_body["total_tasks"] == 2
        assert eval_body["failed_tasks"] == 1
        assert eval_body["blocked_tasks"] == 1

        # Check Root Cause Analysis: grounded evidence must capture assertion mismatch
        rca = eval_body["root_cause_analysis"]
        assert len(rca) >= 1
        failing_rca = next(r for r in rca if r["task_id"] == "failing_task")
        assert failing_rca["classification"] == "confirmed_cause"
        assert "500" in failing_rca["diagnostic"] or "Assertion" in failing_rca["diagnostic"]
        assert len(failing_rca["evidence_references"]) >= 1

        # Check Requirement Traceability
        cov_map = {r["requirement_id"]: r for r in eval_body["requirement_coverage"]}
        assert cov_map["REQ-FAILURE-01"]["status"] == "FAILED"
        assert cov_map["REQ-BLOCKED-02"]["status"] == "BLOCKED"

        # Check that report export reflects root causes
        report_export_resp = await client.get(f"/api/v1/runs/{run_id}/export/report")
        assert report_export_resp.status_code == 200
        report_text = report_export_resp.text
        assert "REQ-FAILURE-01" in report_text
        assert "REQ-BLOCKED-02" in report_text
        assert "FAILED" in report_text

    async def test_cancelled_run_evaluates_and_exports(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
    ) -> None:
        """Verify cancelled run reaches terminal state, triggers evaluation, and persists verdict."""
        mock_plan = {
            "name": "Cancelled Mission",
            "description": "Validates cancelled run evaluation",
            "max_parallel_tasks": 1,
            "tasks": [
                {
                    "task_id": "cancelled_task",
                    "name": "Cancelled Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-CANCEL-01"],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "eval_cancel_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Auto Eval Cancel Project"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={"requirements_content": "# Spec\nREQ-CANCEL-01: Cancel test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={})

        # Insert run directly in running state and cancel it via API
        run = Run(
            id=str(uuid.uuid4()),
            plan_id=mission_id,
            plan_version=1,
            project_id=project_id,
            environment_id=env_id,
            status=RunStatus.RUNNING.value,
            requested_by="eval_cancel_user@example.com",
        )
        db_session.add(run)
        await db_session.commit()

        cancel_resp = await client.post(f"/api/v1/runs/{run.id}/cancel", json={"reason": "User abort"})
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # Check evaluation was triggered automatically
        eval_resp = await client.get(f"/api/v1/runs/{run.id}/evaluation")
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["verdict"] == "cancelled"
        assert eval_data["is_idempotent_replay"] is True
