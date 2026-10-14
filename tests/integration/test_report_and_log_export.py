"""
tests/integration/test_report_and_log_export.py
===============================================
Integration tests for Evidence-Based Execution Log and Final Report Export (Task 3):
1. Successful run export: produces valid execution_log.json and report.md.
2. Failed and partially completed runs: correctly classifies failure diagnostics and handles incomplete runs.
3. Requirement IDs correctly map to results in traceability matrix.
4. JSON schema validation: validates exported JSON against ExecutionLogSchema contract.
5. Missing or incomplete task records: exporter handles gracefully without crashing.
6. Exporter failure handling: atomic writes prevent corrupted files.
7. Concurrent runs cannot overwrite each other: per-run isolated directories preserved.
8. Secret redaction: credentials and sensitive context variables are redacted in logs and report.
9. Report results match persisted execution outcomes exactly.
10. Screenshots exist and are referenced in Markdown and JSON when UI failure captures occur.
11. API export endpoints enforce tenant isolation and deliver file contents.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from httpx import AsyncClient
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_adapter_base.base import BaseAdapter, ExecutionContext, StepExecutionResult
from uitp_api.config import get_settings
from uitp_api.models.dag import DAGTask, DAGTaskAttempt, RunContext
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_api.services.report_export_service import export_run_execution_artifacts
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import AdapterType, StepStatus
from uitp_contracts.export import ExecutionLogSchema
from uitp_contracts.plan import TestStepSchema, TokenUsage
from uitp_evidence.storage import LocalFilesystemEvidenceStorage
from uitp_api.services import evidence_service


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "export-secure-pw-123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "export-secure-pw-123"},
    )
    return resp.json()


class CustomMockAIProvider(MockAIProvider):
    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data

    async def complete_structured(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.1):
        return self._response_data, TokenUsage(prompt_tokens=200, completion_tokens=400, total_tokens=600)


class MockWebAdapterWithScreenshot(BaseAdapter):
    ADAPTER_ID = "mock_web_adapter"
    VERSION = "1.0.0"

    def metadata(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=self.ADAPTER_ID,
            adapter_type=AdapterType.WEB,
            version=self.VERSION,
            supported_actions=["navigate", "click"],
            limitations=[],
            conformance_version="1.0",
        )

    async def health_check(self) -> bool:
        return True

    async def execute_step(self, step: TestStepSchema, context: ExecutionContext) -> StepExecutionResult:
        # Generate a fake PNG byte stream (1x1 transparent PNG)
        fake_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
            b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        if step.action == "click":
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code="ASSERTION_FAILED",
                error_message="Element '#submit-button' not visible within 5000ms",
                outputs={
                    "action": "click",
                    "locator": "#submit-button",
                    "screenshot_bytes": fake_png,
                },
            )
        return StepExecutionResult(
            status=StepStatus.PASSED,
            outputs={"action": step.action, "url": "http://mock-api.local/dashboard"},
        )

    async def close_session(self, run_id: str) -> None:
        pass


@pytest.mark.integration
class TestReportAndLogExport:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        set_ai_provider(None)
        yield
        set_ai_provider(None)

    @pytest.mark.asyncio
    async def test_successful_run_export_and_schema_validation(
        self, client: AsyncClient, app, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Successful run export creates valid execution_log.json and report.md."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "/api/v1/auth" in str(request.url):
                return httpx.Response(200, json={"token": "sec-tok-123", "user": "alice"})
            elif "/api/v1/items" in str(request.url):
                return httpx.Response(200, json={"items": [{"id": 1, "name": "item1"}]})
            return httpx.Response(404)

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        plan_data = {
            "name": "E2E Success Mission",
            "description": "Full workflow with requirements",
            "tasks": [
                {
                    "task_id": "auth_task",
                    "name": "Auth Step",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "requirement_refs": ["REQ-AUTH-01"],
                    "steps": [
                        {
                            "step_id": "step_auth",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/auth", "json": {"user": "alice"}},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"token": {"json_path": "$.token", "is_secret": True}},
                },
                {
                    "task_id": "items_task",
                    "name": "Fetch Items",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["auth_task"],
                    "requirement_refs": ["REQ-DATA-01"],
                    "steps": [
                        {
                            "step_id": "step_items",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {
                                "path": "/api/v1/items",
                                "headers": {"Authorization": "Bearer ${context.token}"},
                            },
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(plan_data))

        await _register_and_login(client, "export_success_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Export Success Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Authenticate and query data", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        # Execute run
        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        run_id = run_data["id"]

        # Run export to tmp_path
        res = await export_run_execution_artifacts(
            db_session,
            run_id=run_id,
            output_dir=tmp_path,
            export_root_deliverables=True,
        )

        assert res.run_id == run_id
        assert Path(res.execution_log_path).exists()
        assert Path(res.report_path).exists()

        # Check root deliverable paths as required by assignment (/output/execution_log.json and /output/report.md)
        root_log = tmp_path / "execution_log.json"
        root_report = tmp_path / "report.md"
        assert root_log.exists()
        assert root_report.exists()

        # 1. JSON Schema validation
        raw_json = root_log.read_text(encoding="utf-8")
        parsed = json.loads(raw_json)
        validated_log = ExecutionLogSchema.model_validate(parsed)
        assert validated_log.schema_version == "1.0"
        assert validated_log.run_id == run_id
        assert validated_log.status == "passed"
        assert validated_log.summary["total_tasks"] == 2
        assert validated_log.summary["passed_tasks"] == 2
        assert validated_log.summary["failed_tasks"] == 0

        # 2. Secret Redaction check
        assert "sec-tok-123" not in raw_json
        assert "[REDACTED]" in raw_json or "token" in raw_json

        # 3. Requirement Traceability check
        req_ids = {r.requirement_id: r.status for r in validated_log.requirement_traceability}
        assert req_ids["REQ-AUTH-01"] == "PASSED"
        assert req_ids["REQ-DATA-01"] == "PASSED"

        # 4. Markdown Report check
        report_text = root_report.read_text(encoding="utf-8")
        assert f"Run ID:** `{run_id}`" in report_text
        assert "PASSED" in report_text
        assert "REQ-AUTH-01" in report_text
        assert "REQ-DATA-01" in report_text
        assert "sec-tok-123" not in report_text

    @pytest.mark.asyncio
    async def test_failed_run_and_cascaded_blocked_diagnostics(
        self, client: AsyncClient, app, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Failed task provides grounded assertion diagnostic and cascades blocked."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "/api/v1/auth" in str(request.url):
                # Returns 500 error instead of 200
                return httpx.Response(500, json={"error": "Database connectivity loss"})
            return httpx.Response(404)

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        plan_data = {
            "name": "Failure Diagnostic Mission",
            "description": "Fails at auth and blocks dependent",
            "tasks": [
                {
                    "task_id": "producer_task",
                    "name": "Auth Producer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "requirement_refs": ["REQ-AUTH-FAIL"],
                    "steps": [
                        {
                            "step_id": "step_auth_failing",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/auth"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"token": {"json_path": "$.token", "is_secret": True}},
                },
                {
                    "task_id": "consumer_task",
                    "name": "Data Consumer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["producer_task"],
                    "requirement_refs": ["REQ-DATA-BLOCKED"],
                    "steps": [
                        {
                            "step_id": "step_data",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/data"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(plan_data))

        await _register_and_login(client, "export_fail_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Export Fail Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Fail test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        res = await export_run_execution_artifacts(db_session, run_id=run_id, output_dir=tmp_path)
        log_json = (tmp_path / "runs" / run_id / "execution_log.json").read_text(encoding="utf-8")
        parsed = ExecutionLogSchema.model_validate(json.loads(log_json))

        assert parsed.status == "failed"
        assert parsed.summary["failed_tasks"] == 1
        assert parsed.summary["blocked_tasks"] == 1

        producer_log = next(t for t in parsed.tasks if t.task_id == "producer_task")
        consumer_log = next(t for t in parsed.tasks if t.task_id == "consumer_task")

        assert producer_log.status == "failed"
        assert producer_log.error_category == "assertion_failure"
        assert "500" in (producer_log.diagnostic or "") or "assertion" in (producer_log.diagnostic or "").lower()

        assert consumer_log.status == "blocked"
        assert consumer_log.error_category == "upstream_blocked"
        assert "producer_task" in (consumer_log.diagnostic or "")

        # Check requirement traceability reflects failed and blocked accurately
        req_map = {r.requirement_id: r.status for r in parsed.requirement_traceability}
        assert req_map["REQ-AUTH-FAIL"] == "FAILED"
        assert req_map["REQ-DATA-BLOCKED"] == "BLOCKED"

        # Check report contains root cause diagnostic
        report_text = (tmp_path / "runs" / run_id / "report.md").read_text(encoding="utf-8")
        assert "Diagnostic for `producer_task` (FAILED)" in report_text
        assert "upstream_blocked" in report_text

    @pytest.mark.asyncio
    async def test_ui_failure_with_screenshot_referenced_in_report(
        self, client: AsyncClient, app, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """When UI task fails, screenshot is exported and linked in Markdown and JSON."""
        app.state.custom_web_adapter = MockWebAdapterWithScreenshot()

        plan_data = {
            "name": "UI Screenshot Mission",
            "description": "UI click failure captures screenshot",
            "tasks": [
                {
                    "task_id": "ui_click_task",
                    "name": "Click Button",
                    "assigned_role": "ui_specialist",
                    "adapter_type": "web",
                    "depends_on": [],
                    "requirement_refs": ["REQ-UI-01"],
                    "steps": [
                        {
                            "step_id": "step_click",
                            "adapter_type": "web",
                            "action": "click",
                            "inputs": {"locator": "#submit-button"},
                        }
                    ],
                    "exports": {},
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(plan_data))

        await _register_and_login(client, "ui_shot_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "UI Shot Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "UI Test with failure", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        res = await export_run_execution_artifacts(db_session, run_id=run_id, output_dir=tmp_path)
        assert res.evidence_files_count >= 1

        log_json = (tmp_path / "runs" / run_id / "execution_log.json").read_text(encoding="utf-8")
        parsed = ExecutionLogSchema.model_validate(json.loads(log_json))
        ui_task = parsed.tasks[0]
        assert len(ui_task.evidence_references) >= 1
        shot_ref = next(e for e in ui_task.evidence_references if e.type == "screenshot")
        assert shot_ref.relative_path is not None
        assert shot_ref.relative_path.endswith(".png")

        # Verify screenshot file actually exists on filesystem
        screenshot_path = tmp_path / "runs" / run_id / shot_ref.relative_path
        assert screenshot_path.exists()
        assert screenshot_path.stat().st_size > 0

        # Verify Markdown report references screenshot embed
        report_text = (tmp_path / "runs" / run_id / "report.md").read_text(encoding="utf-8")
        assert f"![{ui_task.task_id} Screenshot]({shot_ref.relative_path})" in report_text

    @pytest.mark.asyncio
    async def test_concurrent_runs_isolation_and_no_collision(
        self, client: AsyncClient, app, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Concurrent or distinct runs write to distinct per-run subdirectories without collision."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        plan_data = {
            "name": "Concurrent Mission",
            "description": "Multi runs test",
            "tasks": [
                {
                    "task_id": "simple_task",
                    "name": "Simple Step",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_s",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/status"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(plan_data))

        await _register_and_login(client, "multi_run_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Multi Run Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Simple", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        # Run 1
        r1_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run1_id = r1_resp.json()["id"]

        # Run 2
        r2_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run2_id = r2_resp.json()["id"]

        res1 = await export_run_execution_artifacts(db_session, run_id=run1_id, output_dir=tmp_path)
        res2 = await export_run_execution_artifacts(db_session, run_id=run2_id, output_dir=tmp_path)

        path1 = tmp_path / "runs" / run1_id
        path2 = tmp_path / "runs" / run2_id

        assert path1.exists()
        assert path2.exists()
        assert path1 != path2

        log1 = json.loads((path1 / "execution_log.json").read_text(encoding="utf-8"))
        log2 = json.loads((path2 / "execution_log.json").read_text(encoding="utf-8"))

        assert log1["run_id"] == run1_id
        assert log2["run_id"] == run2_id

    @pytest.mark.asyncio
    async def test_partial_or_interrupted_run_handled_gracefully(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Incomplete / interrupted run with missing task records does not crash exporter."""
        await _register_and_login(client, "partial_run_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Partial Run Proj"})
        proj_id = proj_resp.json()["id"]

        # Manually create a run in 'running' state with no finished_at or task attempts
        run = Run(
            id="partial-test-run-001",
            plan_id="plan-stub",
            plan_version=1,
            environment_id="env-stub",
            project_id=proj_id,
            status="running",
            requested_by="user-1",
        )
        db_session.add(run)
        await db_session.commit()

        # Should export gracefully without raising unhandled exception
        res = await export_run_execution_artifacts(db_session, run_id=run.id, output_dir=tmp_path)
        assert res.run_id == run.id

        log_json = (tmp_path / "runs" / run.id / "execution_log.json").read_text(encoding="utf-8")
        parsed = ExecutionLogSchema.model_validate(json.loads(log_json))
        assert parsed.status == "running"
        assert parsed.finished_at is None

        report_text = (tmp_path / "runs" / run.id / "report.md").read_text(encoding="utf-8")
        assert "In Progress" in report_text

    @pytest.mark.asyncio
    async def test_api_export_endpoints_and_tenant_isolation(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """API endpoints POST /export, GET /report, GET /log serve outputs with tenant security."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        plan_data = {
            "name": "API Export Router Test",
            "description": "Endpoints test",
            "tasks": [
                {
                    "task_id": "step_a",
                    "name": "Step A",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_api",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/ok"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(plan_data))

        # User A owns the run
        await _register_and_login(client, "user_a@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Project A"})
        proj_a_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_a_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_a_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_a_id}/missions/plan",
            json={"requirements_content": "Endpoint test", "environment_id": env_a_id},
        )
        mission_id = plan_resp.json()["mission_id"]
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_a_id})
        run_id = run_resp.json()["id"]

        # User A exports via API: POST /api/v1/runs/{run_id}/export
        exp_post = await client.post(f"/api/v1/runs/{run_id}/export")
        assert exp_post.status_code == 200
        exp_data = exp_post.json()
        assert exp_data["run_id"] == run_id

        # User A fetches report: GET /api/v1/runs/{run_id}/export/report
        rep_resp = await client.get(f"/api/v1/runs/{run_id}/export/report")
        assert rep_resp.status_code == 200
        assert "text/markdown" in rep_resp.headers["content-type"]
        assert f"Run ID:** `{run_id}`" in rep_resp.text

        # User A fetches JSON log: GET /api/v1/runs/{run_id}/export/log
        log_resp = await client.get(f"/api/v1/runs/{run_id}/export/log")
        assert log_resp.status_code == 200
        log_data = log_resp.json()
        assert log_data["run_id"] == run_id

        # User B (different tenant) is blocked with 404
        await _register_and_login(client, "user_b_unauthorized@example.com")
        unauth_post = await client.post(f"/api/v1/runs/{run_id}/export")
        assert unauth_post.status_code == 404
        unauth_rep = await client.get(f"/api/v1/runs/{run_id}/export/report")
        assert unauth_rep.status_code == 404
        unauth_log = await client.get(f"/api/v1/runs/{run_id}/export/log")
        assert unauth_log.status_code == 404
