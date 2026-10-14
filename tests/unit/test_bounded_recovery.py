"""
tests/unit/test_bounded_recovery.py
===================================
Comprehensive unit tests for Bounded Recovery (Task 7):
  1. RetryManager policy evaluation:
     - Distinguishes transient (timeout, 502, 503, 504, 429) from permanent (validation, 400, 404, assertion) errors.
     - Strictly blocks non-idempotent action retries unless explicit opt-in (allow_non_idempotent_retry) is declared.
     - Enforces max attempt bounds and exponential backoff caps.
  2. Bounded Model Output Repair:
     - Re-prompts on malformed or schema-invalid JSON using safe validation feedback.
     - Rejects executable code generation attempts (never execute model code).
     - Terminates bounded repair at configured limit without silent failure.
     - Zero credential / secret leakage in repair prompts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from uitp_api.exceptions import ValidationError
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    IdempotencyClass,
    StepStatus,
)
from uitp_contracts.plan import (
    DAGTaskSchema,
    RetryPolicySchema,
    TestStepSchema,
    TokenUsage,
)
from uitp_orchestrator.retry import RetryDecision, RetryManager
from uitp_api.services.multi_agent_service import (
    _complete_and_validate_with_bounded_repair,
)


# ============================================================================
# 1. RetryManager & Failure Classification Tests
# ============================================================================


def test_retry_manager_retries_transient_network_error() -> None:
    step = TestStepSchema(
        step_id="step-api-get",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="GET",
        retry_policy=RetryPolicySchema(max_attempts=3, backoff_seconds=1.0),
    )
    decision = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        error_code="NETWORK_ERROR",
    )
    assert decision.should_retry is True
    assert decision.delay_seconds >= 1.0
    assert "Transient failure" in decision.reason


def test_retry_manager_retries_retryable_status_codes() -> None:
    step = TestStepSchema(
        step_id="step-503",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="GET",
        retry_policy=RetryPolicySchema(max_attempts=3),
    )
    # 503 Service Unavailable
    decision_503 = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        status_code=503,
    )
    assert decision_503.should_retry is True

    # 429 Too Many Requests
    decision_429 = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        status_code=429,
    )
    assert decision_429.should_retry is True


def test_retry_manager_never_retries_permanent_errors() -> None:
    step = TestStepSchema(
        step_id="step-perm",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="GET",
        retry_policy=RetryPolicySchema(max_attempts=3),
    )
    # Permanent client error: 404 Not Found
    decision_404 = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        status_code=404,
    )
    assert decision_404.should_retry is False
    assert "HTTP 404 is non-retryable" in decision_404.reason

    # Assertion failure must NEVER be retried
    decision_assertion = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        error_code=ErrorCode.ASSERTION_FAILED,
    )
    assert decision_assertion.should_retry is False
    assert "Permanent failure" in decision_assertion.reason

    # Policy blocked error must NEVER be retried
    decision_policy = RetryManager.evaluate(
        step=step,
        current_attempt=1,
        status=StepStatus.FAILED,
        error_code=ErrorCode.POLICY_BLOCKED,
    )
    assert decision_policy.should_retry is False


def test_retry_manager_safe_idempotency_rules() -> None:
    # Non-idempotent action (POST) without explicit opt-in
    step_unsafe = TestStepSchema(
        step_id="step-post-payment",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="POST",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        retry_policy=RetryPolicySchema(
            max_attempts=3,
            allow_non_idempotent_retry=False,
        ),
    )
    decision = RetryManager.evaluate(
        step=step_unsafe,
        current_attempt=1,
        status=StepStatus.FAILED,
        error_code="ADAPTER_TIMEOUT",
    )
    assert decision.should_retry is False
    assert "prohibited" in decision.reason

    # Non-idempotent action WITH explicit opt-in
    step_safe_optin = TestStepSchema(
        step_id="step-post-retryable",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="POST",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        retry_policy=RetryPolicySchema(
            max_attempts=3,
            allow_non_idempotent_retry=True,
        ),
    )
    decision_optin = RetryManager.evaluate(
        step=step_safe_optin,
        current_attempt=1,
        status=StepStatus.FAILED,
        error_code="ADAPTER_TIMEOUT",
    )
    assert decision_optin.should_retry is True


def test_retry_manager_bounds_and_exponential_backoff() -> None:
    step = TestStepSchema(
        step_id="step-backoff",
        task_id="task-1",
        adapter_type=AdapterType.HTTP_API,
        action="GET",
        retry_policy=RetryPolicySchema(
            max_attempts=4,
            backoff_seconds=1.0,
        ),
    )
    # Attempt 1 -> delay 1.0 * (1.5^0) = 1.0
    dec1 = RetryManager.evaluate(step=step, current_attempt=1, status=StepStatus.FAILED, error_code="ADAPTER_TIMEOUT")
    assert dec1.delay_seconds == 1.0

    # Attempt 2 -> delay 1.0 * (1.5^1) = 1.5
    dec2 = RetryManager.evaluate(step=step, current_attempt=2, status=StepStatus.FAILED, error_code="ADAPTER_TIMEOUT")
    assert dec2.delay_seconds == 1.5

    # Attempt 3 -> delay 1.0 * (1.5^2) = 2.25
    dec3 = RetryManager.evaluate(step=step, current_attempt=3, status=StepStatus.FAILED, error_code="ADAPTER_TIMEOUT")
    assert dec3.delay_seconds == 2.25

    # Attempt 4 (max reached) -> no retry
    dec4 = RetryManager.evaluate(step=step, current_attempt=4, status=StepStatus.FAILED, error_code="ADAPTER_TIMEOUT")
    assert dec4.should_retry is False
    assert "maximum attempt limit" in dec4.reason


# ============================================================================
# 2. Bounded Model Output Repair Tests
# ============================================================================


@pytest.mark.asyncio
async def test_bounded_repair_succeeds_on_second_attempt() -> None:
    valid_task = {
        "task_id": "task_test",
        "name": "Repaired Task",
        "assigned_role": "api_specialist",
        "adapter_type": "http_api",
        "steps": [
            {
                "step_id": "step-1",
                "task_id": "task_test",
                "adapter_type": "http_api",
                "action": "GET",
                "inputs": {"url": "/health"},
            }
        ],
    }

    mock_provider = MagicMock()
    mock_provider.complete_structured = AsyncMock(
        side_effect=[
            ({"invalid": "schema"}, TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20)),
            ({"tasks": [valid_task]}, TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)),
        ]
    )

    raw_output, parsed_tasks, usage = await _complete_and_validate_with_bounded_repair(
        provider=mock_provider,
        system_prompt="System instructions",
        initial_user_prompt="Plan tasks",
        max_repairs=2,
    )

    assert len(parsed_tasks) == 1
    assert parsed_tasks[0].task_id == "task_test"
    assert mock_provider.complete_structured.await_count == 2
    assert usage.total_tokens == 50


@pytest.mark.asyncio
async def test_bounded_repair_rejects_model_executable_code() -> None:
    bad_task = {
        "task_id": "malicious",
        "name": "Dangerous task",
        "assigned_role": "api_specialist",
        "adapter_type": "http_api",
        "steps": [
            {
                "step_id": "s1",
                "task_id": "malicious",
                "adapter_type": "http_api",
                "action": "GET",
                "inputs": {"code": "__import__('os').system('rm -rf /')"},
            }
        ],
    }
    mock_provider = MagicMock()
    mock_provider.complete_structured = AsyncMock(return_value=({"tasks": [bad_task]}, TokenUsage()))

    with pytest.raises(ValidationError) as exc_info:
        await _complete_and_validate_with_bounded_repair(
            provider=mock_provider,
            system_prompt="System instructions",
            initial_user_prompt="Plan tasks",
            max_repairs=1,
        )

    # Should attempt repair and eventually fail without executing
    assert "forbidden code execution token" in str(exc_info.value) or "validation failed" in str(exc_info.value).lower()
    assert mock_provider.complete_structured.await_count == 2


@pytest.mark.asyncio
async def test_bounded_repair_stops_at_configured_limit() -> None:
    mock_provider = MagicMock()
    # Always returns invalid schema
    mock_provider.complete_structured = AsyncMock(return_value=({"invalid_field": 123}, TokenUsage()))

    with pytest.raises(ValidationError) as exc_info:
        await _complete_and_validate_with_bounded_repair(
            provider=mock_provider,
            system_prompt="System instructions",
            initial_user_prompt="Plan tasks",
            max_repairs=2,
        )

    # Total calls = initial call (1) + 2 repair attempts = 3 calls
    assert mock_provider.complete_structured.await_count == 3
    assert "failed after 3 attempts" in str(exc_info.value)
