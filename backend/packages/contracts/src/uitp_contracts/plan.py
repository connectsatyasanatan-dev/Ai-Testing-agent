"""
uitp_contracts.plan
===================
Schemas for test plans, steps, assertions, and retry policies.

These are the canonical types shared between:
  - packages/contracts (source of truth)
  - apps/api (create/validate/approve plans)
  - packages/adapters/* (validate and execute steps)
  - apps/web (via generated TypeScript types)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from uitp_contracts.enums import (
    AdapterType,
    AgentRole,
    AssertionType,
    IdempotencyClass,
    PlanStatus,
    RiskLevel,
    RunStatus,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Assertion
# ---------------------------------------------------------------------------


class AssertionSchema(BaseModel):
    """A single deterministic assertion on a step result."""

    model_config = ConfigDict(frozen=True)

    type: AssertionType
    """The kind of check to perform."""

    value: str | int | float | bool | None = None
    """Expected value, path, or threshold (interpretation depends on type)."""

    path: str | None = None
    """JSON path expression for json_path_* assertion types."""

    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    """JSON Schema object for the json_schema assertion type."""

    message: str | None = None
    """Optional description shown in the run report when this assertion fails."""


class AssertionResultSchema(BaseModel):
    """Result of evaluating a single assertion against step outputs."""

    model_config = ConfigDict(frozen=True)

    type: AssertionType | str
    passed: bool
    message: str | None = None
    expected: Any = None
    actual: Any = None
    path: str | None = None
    error_code: str | None = None
    duration_ms: float | None = None


class StepAssertionsOutcome(BaseModel):
    """Aggregated outcome of evaluating all assertions for a step."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    total_assertions: int
    passed_count: int
    failed_count: int
    results: list[AssertionResultSchema] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


class RetryPolicySchema(BaseModel):
    """Controls how and whether a step is retried on failure.

    For non_idempotent steps, allow_non_idempotent_retry must be
    explicitly true before retries are attempted (ARCHITECTURE.md §5b).
    """

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=10)
    """Maximum total attempts including the first. 1 = no retry."""

    backoff_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    """Fixed wait between attempts. Exponential backoff is not supported yet."""

    retryable_status_codes: list[int] = Field(default_factory=list)
    """HTTP status codes that should trigger a retry (HTTP adapter only)."""

    allow_non_idempotent_retry: bool = False
    """Explicitly opt in to retrying non_idempotent steps.
    Setting this to True must be a deliberate plan-author decision.
    """


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class TestStepSchema(BaseModel):
    """A single step in a test plan.

    The step_id must be unique within a plan version.
    Adapters receive this schema verbatim from the orchestrator.
    """

    __test__ = False
    model_config = ConfigDict(frozen=True)

    step_id: str = Field(..., min_length=1, max_length=128)
    """Stable identifier for this step within the plan. Used in evidence links."""

    adapter_type: AdapterType
    """Which adapter handles this step."""

    action: str = Field(..., min_length=1, max_length=128)
    """Allowlisted action name (e.g. 'navigate', 'click', 'http_request').
    Arbitrary code strings are not permitted (ADR-007).
    """

    inputs: dict[str, Any] = Field(default_factory=dict)
    """Action-specific inputs validated by the adapter's action schema."""

    assertions: list[AssertionSchema] = Field(default_factory=list)
    """Deterministic checks evaluated after the action completes."""

    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    """Maximum wall-clock time for this step. The adapter must respect this."""

    retry_policy: RetryPolicySchema = Field(default_factory=RetryPolicySchema)
    """Controls retries. Default: 1 attempt, no retry."""

    idempotency_class: IdempotencyClass = IdempotencyClass.UNKNOWN
    """Explicit classification set by the plan author or AI generator.
    NOT inferred from the HTTP method (ARCHITECTURE.md §5b).
    """

    risk_level: RiskLevel = RiskLevel.LOW
    """Used by the policy engine. HIGH/CRITICAL may require explicit approval."""

    description: str | None = None
    """Optional human-readable description for the plan editor and report."""

    @field_validator("action")
    @classmethod
    def action_must_not_be_code(cls, v: str) -> str:
        """Reject obvious attempt to embed executable code in action names."""
        forbidden = ("import ", "exec(", "eval(", "__", "subprocess", "os.system")
        lower = v.lower()
        for token in forbidden:
            if token in lower:
                msg = (
                    f"Action '{v}' appears to contain executable code. "
                    "Only allowlisted action names are permitted (ADR-007)."
                )
                raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class TestPlanSchema(BaseModel):
    """A versioned, declarative test plan."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    """UUIDv4 identifier assigned by the platform."""

    project_id: str
    """Owning project (UUIDv4)."""

    name: str = Field(..., min_length=1, max_length=255)

    version: int = Field(..., ge=1)
    """Monotonically increasing version number within this plan_id."""

    adapter_type: AdapterType
    """Primary adapter for this plan. All steps must target this adapter type."""

    status: PlanStatus = PlanStatus.DRAFT

    steps: list[TestStepSchema] = Field(..., min_length=1)
    """Ordered list of steps. A plan with zero steps is invalid."""

    description: str | None = None
    source: str | None = None
    """'manual' | 'ai_generated' — informational only."""

    requires_approval: bool = True
    """Always true. AI-generated plans cannot bypass the approval gate (ADR-003)."""


# ---------------------------------------------------------------------------
# Plan creation request (API input)
# ---------------------------------------------------------------------------


class CreatePlanRequest(BaseModel):
    """Request body for POST /api/v1/projects/{project_id}/plans."""

    name: str = Field(..., min_length=1, max_length=255)
    adapter_type: AdapterType
    steps: list[TestStepSchema] = Field(..., min_length=1, max_length=100)
    description: str | None = None


# ---------------------------------------------------------------------------
# Plan version creation request
# ---------------------------------------------------------------------------


class CreatePlanVersionRequest(BaseModel):
    """Request body for POST /api/v1/plans/{plan_id}/versions."""

    steps: list[TestStepSchema] = Field(..., min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


# ---------------------------------------------------------------------------
# Plan approval request
# ---------------------------------------------------------------------------


class ApprovePlanRequest(BaseModel):
    """Request body for POST /api/v1/plans/{plan_id}/approve.

    The caller must supply the exact version number to prevent TOCTOU
    approval of a different version than was reviewed (SECURITY.md §3).
    """

    version: int = Field(..., ge=1)
    """The exact plan version being approved. Must match current draft or pending_review version."""


class SubmitReviewRequest(BaseModel):
    """Request body for POST /api/v1/plans/{plan_id}/submit-review."""

    version: int = Field(..., ge=1)


class RejectPlanRequest(BaseModel):
    """Request body for POST /api/v1/plans/{plan_id}/reject."""

    version: int = Field(..., ge=1)
    reason: str | None = None


class UpdatePlanRequest(BaseModel):
    """Request body for PUT /api/v1/plans/{plan_id} or PUT /api/v1/plans/{plan_id}/versions/{version}."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    steps: list[TestStepSchema] = Field(..., min_length=1, max_length=100)


class ValidationIssue(BaseModel):
    """Detailed validation issue for inline feedback in the builder UI."""

    model_config = ConfigDict(frozen=True)

    field: str | None = None
    step_id: str | None = None
    message: str
    severity: str = "error"  # "error" | "warning"


class ValidatePlanRequest(BaseModel):
    """Request body for plan validation dry-run endpoints."""

    adapter_type: AdapterType
    steps: list[TestStepSchema] = Field(default_factory=list)
    environment_id: str | None = None


class PlanValidationResponse(BaseModel):
    """Outcome of server-side plan and step validation."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan version list responses
# ---------------------------------------------------------------------------


class PlanVersionSummary(BaseModel):
    """Summary of a single plan version for the version history listing."""

    model_config = ConfigDict(frozen=True)

    version: int
    status: PlanStatus | str
    name: str | None = None
    step_count: int = 0
    approved_by: str | None = None
    approved_at: str | None = None
    created_at: str


class PlanVersionsResponse(BaseModel):
    """Response body for GET /api/v1/plans/{plan_id}/versions."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    versions: list[PlanVersionSummary]


# ---------------------------------------------------------------------------
# AI Plan Generation (P5-00, P5-02)
# ---------------------------------------------------------------------------


class GeneratePlanRequest(BaseModel):
    """Request body for POST /api/v1/projects/{project_id}/plans/generate."""

    model_config = ConfigDict(frozen=True)

    intent: str = Field(..., min_length=1, max_length=4000, description="Natural language intent or user story")
    adapter_type: AdapterType = Field(..., description="Target adapter ('http_api' or 'web')")
    environment_id: str | None = Field(default=None, description="Optional target environment ID")
    spec_content: str | None = Field(default=None, max_length=16384, description="Optional API spec / criteria text")


class BlockedStep(BaseModel):
    """Step rejected by post-generation deterministic policy checks."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    action: str
    reason: str
    severity: str = "error"


class GeneratePlanResponse(BaseModel):
    """Response body for POST /api/v1/projects/{project_id}/plans/generate."""

    model_config = ConfigDict(frozen=True)

    status: str = "draft"
    plan_id: str
    name: str
    version: int = 1
    adapter_type: AdapterType
    environment_id: str | None = None
    steps: list[TestStepSchema]
    blocked_steps: list[BlockedStep] = Field(default_factory=list)
    requires_approval: bool = True
    message: str = "Draft generated. Review, edit if needed, and approve before execution."


# ---------------------------------------------------------------------------
# Multi-Agent Orchestration & DAG Contracts
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token usage and cost telemetry for an LLM generation call."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = None


class ContextExportDef(BaseModel):
    """Defines a dynamic variable exported from step outputs for downstream task chaining."""

    model_config = ConfigDict(frozen=True)

    json_path: str = Field(..., description="JSONPath expression into step output, e.g. '$.order_id'")
    is_secret: bool = Field(default=False, description="True if value contains sensitive token/password to be redacted")


class DAGTaskSchema(BaseModel):
    """A distinct task unit within a multi-agent testing mission DAG."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., pattern=r"^[a-z0-9_]{2,64}$", description="Unique lowercase task identifier within the DAG")
    name: str = Field(..., max_length=255, description="Human-readable task title")
    assigned_role: AgentRole = Field(..., description="Specialist agent role executing this task")
    adapter_type: AdapterType = Field(..., description="Target adapter (http_api or web)")
    depends_on: list[str] = Field(default_factory=list, description="List of parent task_ids that must succeed before this task runs")
    steps: list[TestStepSchema] = Field(default_factory=list, description="Declarative test steps for this task")
    exports: dict[str, ContextExportDef] = Field(default_factory=dict, description="Variables exported to downstream tasks")
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    requirement_refs: list[str] = Field(default_factory=list, description="Requirement keys covered by this task")


class MissionDAGPlan(BaseModel):
    """Complete multi-agent test plan DAG."""

    model_config = ConfigDict(frozen=True)

    mission_id: str
    name: str
    description: str | None = None
    tasks: list[DAGTaskSchema]
    max_parallel_tasks: int = Field(default=4, ge=1, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True


class MultiAgentMissionRequest(BaseModel):
    """Request body for multi-agent autonomous testing mission planning."""

    model_config = ConfigDict(frozen=True)

    requirements_content: str = Field(..., min_length=1, max_length=65536, description="Markdown specification or requirements text")
    openapi_spec: str | None = Field(default=None, max_length=262144, description="OpenAPI JSON or YAML specification")
    scenarios_content: str | None = Field(default=None, max_length=65536, description="Test scenarios JSON or criteria")
    target_urls: list[str] = Field(default_factory=list, description="Target application URLs to test")
    environment_id: str = Field(..., description="Target registered project environment ID")
    max_parallel_tasks: int = Field(default=4, ge=1, le=10)


class TaskExecutionOutcome(BaseModel):
    """Outcome of an individual DAG task run."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    status: TaskStatus
    step_attempts: list[dict[str, Any]] = Field(default_factory=list)
    captured_context: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    error_message: str | None = None


class RequirementCoverageStatus(BaseModel):
    """Requirement traceability coverage item."""

    model_config = ConfigDict(frozen=True)

    requirement_id: str
    status: str = Field(..., description="'PASSED' | 'FAILED' | 'BLOCKED' | 'UNTESTED'")
    tested_by_tasks: list[str] = Field(default_factory=list)
    notes: str | None = None


class MissionEvaluationReport(BaseModel):
    """Evidence-grounded mission evaluation synthesized by Evaluator agent."""

    model_config = ConfigDict(frozen=True)

    mission_id: str
    run_id: str
    verdict: RunStatus
    total_tasks: int
    passed_tasks: int
    failed_tasks: int
    blocked_tasks: int
    requirement_coverage: list[RequirementCoverageStatus] = Field(default_factory=list)
    root_cause_analysis: list[dict[str, Any]] = Field(default_factory=list)
    executive_summary: str
    agent_telemetry: dict[str, Any] = Field(default_factory=dict)


