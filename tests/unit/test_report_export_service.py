"""
tests/unit/test_report_export_service.py
========================================
Unit tests for ReportExportService helper functions:
- Sanitization of data structures and secret stripping
- Error category determination
- Markdown synthesis
- Atomic write mechanics
- Schema validation
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import pytest

from uitp_api.exceptions import ExportError
from uitp_api.services.report_export_service import (
    _atomic_write_file,
    _determine_error_category,
    _generate_markdown_report,
    _sanitize_data,
)
from uitp_contracts.export import (
    AssertionResultSummary,
    ExecutionLogSchema,
    RequirementTraceabilityItem,
    StepExecutionLog,
    TaskEvidenceRef,
    TaskExecutionLog,
)


def test_sanitize_data_strips_secrets_and_sensitive_keys():
    secret_vals = {"my-super-secret-pw", "jwt-token-xyz"}
    input_data = {
        "user": "alice",
        "password": "my-super-secret-pw",
        "authorization": "Bearer jwt-token-xyz",
        "nested": {
            "token": "sensitive-123",
            "message": "Hello my-super-secret-pw in text",
        },
        "items": [
            {"auth_cookie": "cookie-val"},
            "Plain string",
        ],
    }

    sanitized = _sanitize_data(input_data, secret_vals)

    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["message"] == "Hello [REDACTED] in text"
    assert sanitized["items"][0]["auth_cookie"] == "[REDACTED]"
    assert sanitized["items"][1] == "Plain string"
    assert sanitized["user"] == "alice"


def test_determine_error_category_priorities():
    # Passed status
    assert _determine_error_category("passed", None, [], []) is None

    # Blocked status
    assert _determine_error_category("blocked", "Upstream fail", [], []) == "upstream_blocked"

    # Timeout
    assert _determine_error_category("failed", "Step timed out after 30s", [], []) == "timeout"

    # Context export error
    assert _determine_error_category("failed", "Export extraction failed: missing token", [], []) == "context_error"

    # Policy blocked
    assert _determine_error_category("failed", "Target not allowlisted", [], []) == "policy_blocked"

    # Assertion failure in step
    step_with_fail = StepExecutionLog(
        step_id="s1",
        action="get",
        adapter_type="http_api",
        status="failed",
        assertions=[
            AssertionResultSummary(assertion_type="status_code", status="failed", expected=200, actual=404)
        ],
    )
    assert _determine_error_category("failed", None, [step_with_fail], []) == "assertion_failure"

    # Schema validation failure in step
    schema_step_fail = StepExecutionLog(
        step_id="s2",
        action="get",
        adapter_type="http_api",
        status="failed",
        assertions=[
            AssertionResultSummary(assertion_type="schema_match", status="failed", message="Missing property 'id'")
        ],
    )
    assert _determine_error_category("failed", None, [schema_step_fail], []) == "schema_validation_failure"

    # Insufficient evidence
    assert _determine_error_category("failed", None, [], []) == "insufficient_evidence"


def test_atomic_write_file_guarantees(tmp_path: Path):
    target_file = tmp_path / "test_dir" / "output.txt"

    # Initial write
    _atomic_write_file(target_file, "First content")
    assert target_file.read_text(encoding="utf-8") == "First content"

    # Overwrite write
    _atomic_write_file(target_file, "Overwritten content")
    assert target_file.read_text(encoding="utf-8") == "Overwritten content"

    # Ensure no temp files remained
    assert len(list(target_file.parent.glob("*.tmp.*"))) == 0


def test_atomic_write_file_handles_failure(tmp_path: Path, monkeypatch):
    target_file = tmp_path / "fail_dir" / "out.txt"

    import os
    def bad_replace(src, dst):
        raise OSError("Permission denied")

    monkeypatch.setattr(os, "replace", bad_replace)

    with pytest.raises(ExportError, match="Failed to atomically write"):
        _atomic_write_file(target_file, "fail content")

    # Temp file should be cleaned up
    if target_file.parent.exists():
        assert len(list(target_file.parent.glob("*.tmp.*"))) == 0


def test_markdown_report_formatting():
    log_schema = ExecutionLogSchema(
        schema_version="1.0",
        run_id="run-12345",
        mission_id="mission-abc",
        plan_version=2,
        environment_id="staging-env",
        project_id="proj-99",
        status="failed",
        started_at=datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 14, 10, 0, 15, tzinfo=UTC),
        duration_seconds=15.0,
        summary={
            "total_tasks": 2,
            "passed_tasks": 1,
            "failed_tasks": 1,
            "blocked_tasks": 0,
            "skipped_tasks": 0,
        },
        requirement_traceability=[
            RequirementTraceabilityItem(
                requirement_id="REQ-LOGIN",
                status="FAILED",
                tested_by_tasks=["t1"],
                notes="Assertion failed: status_code 401 != 200",
            )
        ],
        tasks=[
            TaskExecutionLog(
                task_id="t1",
                name="Login Task",
                role="api_specialist",
                adapter_type="http_api",
                status="failed",
                attempt_number=1,
                error_category="assertion_failure",
                diagnostic="Assertion failed: status_code expected 200, got 401",
                steps=[
                    StepExecutionLog(
                        step_id="s1",
                        action="post",
                        adapter_type="http_api",
                        status="failed",
                        http_status=401,
                        error_message="HTTP 401 Unauthorized",
                    )
                ],
                evidence_references=[
                    TaskEvidenceRef(
                        artifact_id="art-shot-1",
                        type="screenshot",
                        relative_path="evidence/art-shot-1.png",
                    )
                ],
            )
        ],
    )

    report_md = _generate_markdown_report(log_schema, "E2E Mission Plan", "Staging Environment")

    assert "# Test Execution Report: E2E Mission Plan" in report_md
    assert "Run ID:** `run-12345`" in report_md
    assert "Overall Verdict:** **`FAILED`**" in report_md
    assert "| `REQ-LOGIN` | **FAILED** | `t1` | Assertion failed: status_code 401 != 200 |" in report_md
    assert "### Diagnostic for `t1` (FAILED)" in report_md
    assert "Failure Screenshot: ![t1 Screenshot](evidence/art-shot-1.png)" in report_md
