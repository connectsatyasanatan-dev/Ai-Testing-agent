"""
uitp_contracts.export
====================
Versioned schemas for evidence-based execution logs and export metadata.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.enums import AdapterType, AgentRole, RunStatus, TaskStatus


class TaskEvidenceRef(BaseModel):
    """Reference to stored evidence artifact for a task or step attempt."""

    model_config = ConfigDict(frozen=True)

    artifact_id: str
    type: str = Field(..., description="Evidence type: screenshot, response_log, request_log, trace, etc.")
    relative_path: str | None = Field(default=None, description="Relative file path within export directory")
    checksum: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None


class AssertionResultSummary(BaseModel):
    """Assertion evaluation summary."""

    model_config = ConfigDict(frozen=True)

    assertion_type: str
    status: str = Field(..., description="'passed' | 'failed'")
    target: str | None = None
    expected: Any = None
    actual: Any = None
    message: str | None = None


class StepExecutionLog(BaseModel):
    """Granular execution record of an individual step."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    action: str
    adapter_type: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    error_message: str | None = None
    inputs_summary: dict[str, Any] = Field(default_factory=dict)
    outputs_summary: dict[str, Any] = Field(default_factory=dict)
    http_status: int | None = None
    schema_validation: dict[str, Any] | None = None
    ui_action_outcome: dict[str, Any] | None = None
    assertions: list[AssertionResultSummary] = Field(default_factory=list)
    evidence_references: list[TaskEvidenceRef] = Field(default_factory=list)


class TaskExecutionLog(BaseModel):
    """Complete execution record of a task within a DAG or plan."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    name: str
    role: str
    adapter_type: str
    status: str
    attempt_number: int = 1
    depends_on: list[str] = Field(default_factory=list)
    requirement_refs: list[str] = Field(default_factory=list)
    priority: str = "P1"
    is_smoke: bool = False
    is_regression: bool = True
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0.0
    steps: list[StepExecutionLog] = Field(default_factory=list)
    retries_count: int = 0
    error_category: str | None = None
    diagnostic: str | None = None
    evidence_references: list[TaskEvidenceRef] = Field(default_factory=list)


class RequirementTraceabilityItem(BaseModel):
    """Requirement traceability item mapping specification to task outcomes."""

    model_config = ConfigDict(frozen=True)

    requirement_id: str
    status: str = Field(..., description="'PASSED' | 'FAILED' | 'BLOCKED' | 'UNTESTED'")
    tested_by_tasks: list[str] = Field(default_factory=list)
    notes: str | None = None


class ExecutionLogSchema(BaseModel):
    """Versioned, validated schema for execution_log.json."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default="1.0", description="Execution log format version")
    run_id: str
    mission_id: str
    plan_version: int
    environment_id: str
    project_id: str
    status: str
    suite_type: str = "all"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="Task counts: total, passed, failed, blocked, skipped",
    )
    requirement_traceability: list[RequirementTraceabilityItem] = Field(default_factory=list)
    tasks: list[TaskExecutionLog] = Field(default_factory=list)
    retry_history: list[dict[str, Any]] = Field(default_factory=list)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExportResult(BaseModel):
    """Result summary of an export execution."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    output_dir: str
    execution_log_path: str
    report_path: str
    evidence_files_count: int
    exported_at: datetime
