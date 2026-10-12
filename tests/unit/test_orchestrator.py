"""
tests.unit.test_orchestrator
============================
Unit tests for uitp_orchestrator:
  - Run and step state machine transitions and terminal boundaries.
  - Bounded retry manager enforcing strict idempotency rules.
  - RunQueue abstraction and mock behavior.
"""
from __future__ import annotations

import pytest

from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    IdempotencyClass,
    RiskLevel,
    RunStatus,
    StepStatus,
)
from uitp_contracts.plan import RetryPolicySchema, TestStepSchema
from uitp_orchestrator.exceptions import (
    InvalidStateTransitionError,
    QueueUnavailableError,
)
from uitp_orchestrator.queue import RunQueue
from uitp_orchestrator.retry import RetryManager
from uitp_orchestrator.state_machine import RunStateMachine


def _create_test_step(
    idempotency_class: IdempotencyClass = IdempotencyClass.SAFE,
    max_attempts: int = 3,
) -> TestStepSchema:
    return TestStepSchema(
        step_id="step-1",
        adapter_type=AdapterType.HTTP_API,
        action="GET",
        inputs={"path": "/health"},
        assertions=[],
        timeout_seconds=5.0,
        retry_policy=RetryPolicySchema(
            max_attempts=max_attempts,
            backoff_seconds=0.1,
        ),
        idempotency_class=idempotency_class,
        risk_level=RiskLevel.LOW,
    )


class TestRunStateMachine:
    def test_valid_run_transitions(self) -> None:
        # queued -> running
        assert RunStateMachine.can_transition(RunStatus.QUEUED, RunStatus.RUNNING)
        RunStateMachine.assert_valid_transition(RunStatus.QUEUED, RunStatus.RUNNING)

        # running -> passed
        assert RunStateMachine.can_transition(RunStatus.RUNNING, RunStatus.PASSED)
        RunStateMachine.assert_valid_transition(RunStatus.RUNNING, RunStatus.PASSED)

        # running -> failed
        assert RunStateMachine.can_transition(RunStatus.RUNNING, RunStatus.FAILED)
        RunStateMachine.assert_valid_transition(RunStatus.RUNNING, RunStatus.FAILED)

        # running -> cancelled
        assert RunStateMachine.can_transition(RunStatus.RUNNING, RunStatus.CANCELLED)

        # self-transition is valid (no-op)
        assert RunStateMachine.can_transition(RunStatus.RUNNING, RunStatus.RUNNING)

    def test_terminal_states_cannot_transition(self) -> None:
        terminal_states = [
            RunStatus.PASSED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.SKIPPED,
            RunStatus.INCONCLUSIVE,
            RunStatus.CANCELLED,
        ]
        for term in terminal_states:
            assert RunStateMachine.is_terminal(term)
            assert not RunStateMachine.can_transition(term, RunStatus.RUNNING)
            with pytest.raises(InvalidStateTransitionError) as exc_info:
                RunStateMachine.assert_valid_transition(term, RunStatus.RUNNING)
            assert exc_info.value.current_state == term.value
            assert exc_info.value.target_state == RunStatus.RUNNING.value

    def test_invalid_skip_transition(self) -> None:
        # Cannot jump from queued straight to passed without running
        assert not RunStateMachine.can_transition(RunStatus.QUEUED, RunStatus.PASSED)
        with pytest.raises(InvalidStateTransitionError):
            RunStateMachine.assert_valid_transition(RunStatus.QUEUED, RunStatus.PASSED)

    def test_step_transitions(self) -> None:
        assert RunStateMachine.can_transition_step(StepStatus.PENDING, StepStatus.RUNNING)
        assert RunStateMachine.can_transition_step(StepStatus.RUNNING, StepStatus.PASSED)
        assert RunStateMachine.can_transition_step(StepStatus.RUNNING, StepStatus.FAILED)

        # Passed step cannot transition to running
        assert not RunStateMachine.can_transition_step(StepStatus.PASSED, StepStatus.RUNNING)
        with pytest.raises(InvalidStateTransitionError):
            RunStateMachine.assert_valid_step_transition(StepStatus.PASSED, StepStatus.RUNNING)


class TestRetryManager:
    def test_non_idempotent_operation_never_retried(self) -> None:
        step = _create_test_step(idempotency_class=IdempotencyClass.NON_IDEMPOTENT, max_attempts=3)
        decision = RetryManager.evaluate(
            step=step,
            current_attempt=1,
            status=StepStatus.FAILED,
            error_code="ADAPTER_NETWORK_ERROR",
        )
        assert not decision.should_retry
        assert "Non-idempotent" in decision.reason

    def test_safe_step_retries_transient_infrastructure_error(self) -> None:
        step = _create_test_step(idempotency_class=IdempotencyClass.SAFE, max_attempts=3)
        decision = RetryManager.evaluate(
            step=step,
            current_attempt=1,
            status=StepStatus.FAILED,
            error_code="ADAPTER_NETWORK_ERROR",
        )
        assert decision.should_retry
        assert decision.delay_seconds >= 0.1

    def test_deterministic_assertion_failure_never_retried(self) -> None:
        step = _create_test_step(idempotency_class=IdempotencyClass.SAFE, max_attempts=3)
        decision = RetryManager.evaluate(
            step=step,
            current_attempt=1,
            status=StepStatus.FAILED,
            error_code=ErrorCode.ASSERTION_FAILED,
        )
        assert not decision.should_retry
        assert "not categorized as a transient infrastructure error" in decision.reason

    def test_max_attempts_boundary_enforced(self) -> None:
        step = _create_test_step(idempotency_class=IdempotencyClass.IDEMPOTENT, max_attempts=2)
        # Attempt 1 failed -> retry allowed
        d1 = RetryManager.evaluate(step=step, current_attempt=1, status=StepStatus.FAILED, error_code="NETWORK_ERROR")
        assert d1.should_retry

        # Attempt 2 failed -> max reached
        d2 = RetryManager.evaluate(step=step, current_attempt=2, status=StepStatus.FAILED, error_code="NETWORK_ERROR")
        assert not d2.should_retry
        assert "Exceeded maximum attempt limit" in d2.reason

    def test_passed_step_is_never_retried(self) -> None:
        step = _create_test_step(idempotency_class=IdempotencyClass.SAFE, max_attempts=3)
        decision = RetryManager.evaluate(step=step, current_attempt=1, status=StepStatus.PASSED)
        assert not decision.should_retry


class TestRunQueue:
    def test_mock_queue_enqueue_and_status(self) -> None:
        queue = RunQueue(is_mock=True)
        assert queue.is_connected()
        assert queue.get_queue_length() == 0

        job_id = queue.enqueue_run("run-uuid-123")
        assert job_id == "run:run-uuid-123"
        assert queue.get_queue_length() == 1
        assert queue.get_job_status(job_id) == "queued"
        assert queue.get_job_status("non-existent") is None

    def test_disconnected_redis_raises_queue_unavailable(self) -> None:
        # Connecting to a dead / non-existent port without mock mode
        queue = RunQueue(redis_url="redis://127.0.0.1:54321/0", is_mock=False)
        assert not queue.is_connected()
        with pytest.raises(QueueUnavailableError):
            queue.enqueue_run("run-uuid-456")
