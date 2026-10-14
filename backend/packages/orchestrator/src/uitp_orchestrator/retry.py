"""
uitp_orchestrator.retry
=======================
Bounded retry evaluation for infrastructure failures.

Security & Correctness Rules (AGENTS.md & TASKS.md P1-04 / P3-01):
  - Do not retry non-idempotent operations blindly.
  - A step with idempotency_class=non_idempotent is attempted EXACTLY ONCE;
    retry policies must never be applied to non-idempotent test actions.
  - Retries apply only to transient infrastructure failures (network drops,
    timeouts, 502/503/504 gateways) on safe or idempotent operations.
  - Deterministic assertion failures must never be masked by repeated retries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from uitp_contracts.enums import ErrorCode, IdempotencyClass, StepStatus
from uitp_contracts.plan import RetryPolicySchema, TestStepSchema

logger = logging.getLogger(__name__)

# System-wide hard ceiling for retry attempts to prevent runaway loops
ABSOLUTE_MAX_ATTEMPTS = 5

# Error codes considered transient infrastructure failures eligible for retry
INFRASTRUCTURE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ADAPTER_NETWORK_ERROR",
        "ADAPTER_TIMEOUT",
        "NETWORK_ERROR",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "BAD_GATEWAY",
        "SERVICE_UNAVAILABLE",
        "GATEWAY_TIMEOUT",
    }
)


# Permanent error codes that must NEVER be retried
PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.VALIDATION_ERROR.value,
        ErrorCode.UNSUPPORTED_ACTION.value,
        ErrorCode.POLICY_BLOCKED.value,
        ErrorCode.TARGET_NOT_ALLOWLISTED.value,
        ErrorCode.ASSERTION_FAILED.value,
        ErrorCode.PLAN_NOT_APPROVED.value,
        ErrorCode.AUTHENTICATION_FAILED.value,
        ErrorCode.PERMISSION_DENIED.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
        "ASSERTION_FAILED",
        "VALIDATION_ERROR",
        "UNSUPPORTED_ACTION",
        "POLICY_BLOCKED",
    }
)


@dataclass(frozen=True)
class RetryDecision:
    """Result of evaluating whether a failed step attempt should be retried."""

    should_retry: bool
    reason: str
    delay_seconds: float = 0.0


class RetryManager:
    """Evaluates retry eligibility adhering strictly to idempotency contracts."""

    @classmethod
    def evaluate(
        cls,
        step: TestStepSchema,
        current_attempt: int,
        status: StepStatus | str,
        error_code: ErrorCode | str | None = None,
        status_code: int | None = None,
    ) -> RetryDecision:
        """Determine if a failed step execution should be retried.

        Args:
            step: Declarative test step schema including idempotency and retry policy.
            current_attempt: 1-indexed count of the attempt just completed.
            status: Execution status of the attempt.
            error_code: Optional structured error code returned by adapter.
            status_code: Optional HTTP response status code.

        Returns:
            RetryDecision indicating whether to retry, the reason, and backoff delay.
        """
        step_status = StepStatus(status) if isinstance(status, str) else status

        # 1. Passed, skipped, or cancelled steps are never retried
        if step_status in (StepStatus.PASSED, StepStatus.SKIPPED, StepStatus.CANCELLED):
            return RetryDecision(
                should_retry=False,
                reason=f"Step ended in non-retryable status '{step_status.value}'.",
            )

        # 2. Permanent validation or assertion errors are NEVER retried
        code_str = str(error_code) if error_code is not None else None
        if code_str and (code_str in PERMANENT_ERROR_CODES or "assertion" in code_str.lower()):
            return RetryDecision(
                should_retry=False,
                reason=f"Permanent failure '{code_str}' is non-retryable and not categorized as a transient infrastructure error.",
            )

        # 3. Permanent HTTP status codes (4xx except transient 429) are never retried
        if status_code is not None and 400 <= status_code < 500 and status_code != 429:
            return RetryDecision(
                should_retry=False,
                reason=f"Permanent client error HTTP {status_code} is non-retryable.",
            )

        # 4. NON-IDEMPOTENT GUARD: Never retry non-idempotent operations without explicit opt-in
        policy = step.retry_policy or RetryPolicySchema()
        if step.idempotency_class == IdempotencyClass.NON_IDEMPOTENT:
            if not getattr(policy, "allow_non_idempotent_retry", False):
                logger.warning(
                    "Step '%s' has idempotency_class=non_idempotent without allow_non_idempotent_retry. Retries prohibited.",
                    step.step_id,
                )
                return RetryDecision(
                    should_retry=False,
                    reason="Non-idempotent step action prohibited from automated retries without explicit allow_non_idempotent_retry.",
                )

        # 5. Check global attempt limit ceiling
        max_attempts = min(policy.max_attempts, ABSOLUTE_MAX_ATTEMPTS)
        if current_attempt >= max_attempts:
            return RetryDecision(
                should_retry=False,
                reason=f"Exceeded maximum attempt limit of {max_attempts} (attempt {current_attempt}).",
            )

        # 6. Check if transient infrastructure failure or retryable HTTP status
        is_transient = False
        if code_str is not None:
            is_transient = (
                code_str in INFRASTRUCTURE_ERROR_CODES
                or "timeout" in code_str.lower()
                or "network" in code_str.lower()
                or "connection" in code_str.lower()
            )

        if status_code is not None:
            if status_code in (502, 503, 504, 429):
                is_transient = True
            elif hasattr(policy, "retryable_status_codes") and status_code in policy.retryable_status_codes:
                is_transient = True

        if not is_transient:
            return RetryDecision(
                should_retry=False,
                reason="Failure was not categorized as a transient infrastructure error.",
            )

        # 7. Compute bounded backoff delay with exponential factor
        base_delay = getattr(policy, "backoff_seconds", 1.0)
        coef = getattr(policy, "backoff_coefficient", 1.5)
        raw_delay = base_delay * (coef ** max(0, current_attempt - 1))
        bounded_delay = min(max(0.1, float(raw_delay)), 30.0)

        logger.info(
            "Scheduling retry for step '%s' (attempt %d/%d) after %.2fs delay.",
            step.step_id,
            current_attempt + 1,
            max_attempts,
            bounded_delay,
        )

        return RetryDecision(
            should_retry=True,
            reason=f"Transient failure (error={code_str or status_code}) on {step.idempotency_class.value} step.",
            delay_seconds=bounded_delay,
        )
