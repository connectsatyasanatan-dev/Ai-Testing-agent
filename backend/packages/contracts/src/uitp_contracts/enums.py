"""
uitp_contracts.enums
====================
All shared enumeration types. Every status value, classification, and
typed string used across the platform is defined here — not duplicated
in individual packages.
"""

from enum import StrEnum


class RunStatus(StrEnum):
    """Top-level run outcome status (PRD §9, ADR-008)."""

    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """Per-step execution status."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


class AgentRole(StrEnum):
    """Specialist roles in the multi-agent AI orchestration architecture."""

    PLANNER = "planner"
    API_SPECIALIST = "api_specialist"
    UI_SPECIALIST = "ui_specialist"
    EVALUATOR = "evaluator"
    # Optional extensions:
    SECURITY_SPECIALIST = "security_specialist"
    INTEGRATION_SPECIALIST = "integration_specialist"


class TaskStatus(StrEnum):
    """Lifecycle status of an individual task node in the execution DAG."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"



class PlanStatus(StrEnum):
    """Lifecycle status of a test plan version (supports soft-delete)."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ProjectStatus(StrEnum):
    """Project lifecycle — soft-delete only."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class EnvironmentStatus(StrEnum):
    """Environment lifecycle — soft-delete only."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class AdapterType(StrEnum):
    """Supported adapter targets.

    Only web and http_api are MVP targets (ADR-002).
    Others are planned but not implemented.
    """

    WEB = "web"
    HTTP_API = "http_api"
    MULTI_AGENT_DAG = "multi_agent_dag"
    # Planned — not implemented in MVP:
    MOBILE = "mobile"
    DESKTOP = "desktop"
    CLI = "cli"
    EVENT = "event"
    DATABASE = "database"


class IdempotencyClass(StrEnum):
    """Per-step idempotency classification (ARCHITECTURE.md §5b).

    IMPORTANT: This field is set by the plan author or AI generator.
    It is NOT inferred from the HTTP method alone.
    """

    SAFE = "safe"
    """Read-only; repeating produces no side effects."""

    IDEMPOTENT = "idempotent"
    """Mutation whose repetition produces the same result."""

    NON_IDEMPOTENT = "non_idempotent"
    """Mutation whose repetition creates duplicates or unwanted side effects.
    Default retry policy: do NOT retry unless explicitly overridden.
    """

    UNKNOWN = "unknown"
    """Classification not determinable at plan time. Treated as non_idempotent."""


class RiskLevel(StrEnum):
    """Step risk level — used by the policy engine to gate approvals."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AssertionType(StrEnum):
    """Supported assertion types for the HTTP API adapter (P1-05)."""

    STATUS_CODE = "status_code"
    JSON_PATH_EQUALS = "json_path_equals"
    JSON_PATH_EXISTS = "json_path_exists"
    JSON_PATH_MATCHES = "json_path_matches"
    JSON_SCHEMA = "json_schema"
    HEADER_EQUALS = "header_equals"
    HEADER_EXISTS = "header_exists"
    RESPONSE_TIME_UNDER = "response_time_under"
    # Web adapter assertions (P2-03):
    URL_CONTAINS = "url_contains"
    URL_EQUALS = "url_equals"
    TITLE_CONTAINS = "title_contains"
    TITLE_EQUALS = "title_equals"
    TEXT_VISIBLE = "text_visible"
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_HIDDEN = "element_hidden"


class EvidenceType(StrEnum):
    """Type of captured evidence artifact."""

    SCREENSHOT = "screenshot"
    TRACE = "trace"
    REQUEST_LOG = "request_log"
    RESPONSE_LOG = "response_log"
    CONSOLE_LOG = "console_log"
    HAR = "har"


class AuditEventType(StrEnum):
    """Minimum required audit event types (ARCHITECTURE.md §5a).

    All values in this enum MUST be present in database migrations (P0-04).
    """

    USER_LOGIN = "user_login"
    USER_LOGIN_FAILED = "user_login_failed"
    ROLE_CHANGE = "role_change"
    PROJECT_CREATED = "project_created"
    PROJECT_ARCHIVED = "project_archived"
    ENVIRONMENT_CREATED = "environment_created"
    ENVIRONMENT_MODIFIED = "environment_modified"
    SECRET_REFERENCE_UPDATED = "secret_reference_updated"
    PLAN_CREATED = "plan_created"
    PLAN_APPROVED = "plan_approved"
    PLAN_REVIEW_REQUESTED = "plan_review_requested"
    PLAN_REJECTED = "plan_rejected"
    PLAN_ARCHIVED = "plan_archived"
    RUN_CREATED = "run_created"
    RUN_CANCELLED = "run_cancelled"
    POLICY_BLOCKED = "policy_blocked"
    ARTIFACT_ACCESSED = "artifact_accessed"


class ErrorCode(StrEnum):
    """Stable API error codes (API_CONTRACT.md)."""

    PLAN_NOT_APPROVED = "PLAN_NOT_APPROVED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    TARGET_NOT_ALLOWLISTED = "TARGET_NOT_ALLOWLISTED"
    AI_PROVIDER_NOT_CONFIGURED = "AI_PROVIDER_NOT_CONFIGURED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    UNSUPPORTED_ASSERTION = "UNSUPPORTED_ASSERTION"
