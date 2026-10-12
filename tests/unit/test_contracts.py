"""
tests/unit/test_contracts.py
============================
Unit tests for shared Pydantic schemas in packages/contracts.

Validates that:
- Valid input is accepted
- Invalid input raises ValidationError with a useful message
- Security constraints (no code in action names) are enforced
- Enum values are correct
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from uitp_contracts.enums import (
    AdapterType,
    AuditEventType,
    ErrorCode,
    IdempotencyClass,
    PlanStatus,
    RiskLevel,
    RunStatus,
)
from uitp_contracts.plan import (
    ApprovePlanRequest,
    AssertionSchema,
    CreatePlanRequest,
    RetryPolicySchema,
    TestStepSchema,
)
from uitp_contracts.errors import ErrorDetail, ErrorResponse


# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnumCompleteness:
    def test_audit_event_type_has_15_minimum_values(self) -> None:
        """All 15 required audit event types from ARCHITECTURE.md §5a must exist."""
        required = {
            "user_login", "user_login_failed", "role_change",
            "project_created", "project_archived",
            "environment_created", "environment_modified",
            "secret_reference_updated",
            "plan_created", "plan_approved", "plan_archived",
            "run_created", "run_cancelled",
            "policy_blocked", "artifact_accessed",
        }
        actual = {e.value for e in AuditEventType}
        assert required.issubset(actual), f"Missing audit events: {required - actual}"

    def test_error_code_has_plan_not_approved(self) -> None:
        assert ErrorCode.PLAN_NOT_APPROVED == "PLAN_NOT_APPROVED"

    def test_idempotency_class_values(self) -> None:
        assert set(IdempotencyClass) == {
            IdempotencyClass.SAFE,
            IdempotencyClass.IDEMPOTENT,
            IdempotencyClass.NON_IDEMPOTENT,
            IdempotencyClass.UNKNOWN,
        }


# ---------------------------------------------------------------------------
# AssertionSchema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAssertionSchema:
    def test_valid_status_code_assertion(self) -> None:
        a = AssertionSchema(type="status_code", value=200)
        assert a.type.value == "status_code"

    def test_valid_json_path_assertion(self) -> None:
        a = AssertionSchema(type="json_path_equals", path="$.user.id", value="123")
        assert a.path == "$.user.id"

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            AssertionSchema(type="not_a_real_type")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RetryPolicySchema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryPolicySchema:
    def test_default_is_one_attempt_no_retry(self) -> None:
        policy = RetryPolicySchema()
        assert policy.max_attempts == 1
        assert policy.allow_non_idempotent_retry is False

    def test_max_attempts_bounded(self) -> None:
        with pytest.raises(ValidationError):
            RetryPolicySchema(max_attempts=11)  # exceeds le=10

    def test_max_attempts_minimum(self) -> None:
        with pytest.raises(ValidationError):
            RetryPolicySchema(max_attempts=0)  # below ge=1


# ---------------------------------------------------------------------------
# TestStepSchema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTestStepSchema:
    def _valid_step(self, **overrides: object) -> TestStepSchema:
        defaults: dict[str, object] = {
            "step_id": "step-1",
            "adapter_type": AdapterType.HTTP_API,
            "action": "http_request",
            "inputs": {"method": "GET", "url": "https://api.example.com/users"},
            "idempotency_class": IdempotencyClass.SAFE,
            "risk_level": RiskLevel.LOW,
        }
        defaults.update(overrides)
        return TestStepSchema(**defaults)  # type: ignore[arg-type]

    def test_valid_step_accepted(self) -> None:
        step = self._valid_step()
        assert step.step_id == "step-1"
        assert step.idempotency_class == IdempotencyClass.SAFE

    def test_empty_step_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._valid_step(step_id="")

    def test_non_idempotent_default_no_retry(self) -> None:
        step = self._valid_step(
            idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
            retry_policy=RetryPolicySchema(max_attempts=3),
        )
        # Non-idempotent step with retry policy — allow_non_idempotent_retry
        # must be False by default, meaning the adapter should not auto-retry.
        assert step.retry_policy.allow_non_idempotent_retry is False
        assert step.retry_policy.max_attempts == 3  # policy is set but not applied

    def test_action_with_exec_code_rejected(self) -> None:
        with pytest.raises(ValidationError, match="executable code"):
            self._valid_step(action="exec(import os)")

    def test_action_with_import_rejected(self) -> None:
        with pytest.raises(ValidationError, match="executable code"):
            self._valid_step(action="import subprocess")

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValidationError):
            self._valid_step(timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            self._valid_step(timeout_seconds=301.0)

    def test_idempotency_class_defaults_to_unknown(self) -> None:
        step = TestStepSchema(
            step_id="s",
            adapter_type=AdapterType.HTTP_API,
            action="http_request",
        )
        assert step.idempotency_class == IdempotencyClass.UNKNOWN


# ---------------------------------------------------------------------------
# ApprovePlanRequest — TOCTOU guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovePlanRequest:
    def test_valid(self) -> None:
        req = ApprovePlanRequest(version=3)
        assert req.version == 3

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ApprovePlanRequest(version=0)


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorResponse:
    def test_plan_not_approved_error(self) -> None:
        err = ErrorResponse(
            error=ErrorDetail(
                code=ErrorCode.PLAN_NOT_APPROVED,
                message="The requested plan version is not approved.",
                request_id="req_abc",
            )
        )
        assert err.error.code == "PLAN_NOT_APPROVED"

    def test_message_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ErrorDetail(code=ErrorCode.VALIDATION_ERROR)  # type: ignore[call-arg]


TestStepSchemaValidation = TestTestStepSchema
TestErrorContracts = TestErrorResponse
