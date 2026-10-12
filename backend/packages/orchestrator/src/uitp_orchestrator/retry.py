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
    ) -> RetryDecision:
        """Determine if a failed step execution should be retried.

        Args:
            step: Declarative test step schema including idempotency and retry policy.
            current_attempt: 1-indexed count of the attempt just completed.
            status: Execution status of the attempt.
            error_code: Optional structured error code returned by adapter.

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

        # 2. NON-IDEMPOTENT GUARD: Never retry non-idempotent operations
        if step.idempotency_class == IdempotencyClass.NON_IDEMPOTENT:
            logger.warning(
                "Step '%s' has idempotency_class=non_idempotent. Retries are strictly prohibited.",
                step.step_id,
            )
            return RetryDecision(
                should_retry=False,
                reason="Non-idempotent step action prohibited from automated retries.",
            )

        # 3. Check attempt limit from policy
        policy = step.retry_policy or RetryPolicySchema()
        max_attempts = min(policy.max_attempts, ABSOLUTE_MAX_ATTEMPTS)

        if current_attempt >= max_attempts:
            return RetryDecision(
                should_retry=False,
                reason=f"Exceeded maximum attempt limit of {max_attempts} (attempt {current_attempt}).",
            )

        # 4. Only infrastructure failures are retryable
        is_infra_error = False
        if error_code is not None:
            code_str = str(error_code)
            is_infra_error = (
                error_code in INFRASTRUCTURE_ERROR_CODES
                or code_str in INFRASTRUCTURE_ERROR_CODES
                or "timeout" in code_str.lower()
                or "network" in code_str.lower()
            )

        if not is_infra_error:
            return RetryDecision(
                should_retry=False,
                reason="Failure was not categorized as a transient infrastructure error.",
            )

        # 5. Compute backoff delay
        delay = getattr(policy, "backoff_seconds", 1.0)
        bounded_delay = min(max(0.1, float(delay)), 30.0)

        logger.info(
            "Scheduling retry for step '%s' (attempt %d/%d) after %.2fs delay.",
            step.step_id,
            current_attempt + 1,
            max_attempts,
            bounded_delay,
        )

        return RetryDecision(
            should_retry=True,
            reason=f"Transient infrastructure error on {step.idempotency_class.value} step.",
            delay_seconds=bounded_delay,
        )
