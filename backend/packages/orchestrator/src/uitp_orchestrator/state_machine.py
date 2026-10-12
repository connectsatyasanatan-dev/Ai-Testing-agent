"""
uitp_orchestrator.state_machine
===============================
Deterministic state machine validating run and step lifecycle transitions.
Prevents illegal state regressions, race conditions, and phantom state changes.
"""
from __future__ import annotations

from uitp_contracts.enums import RunStatus, StepStatus
from uitp_orchestrator.exceptions import InvalidStateTransitionError

# ── Terminal and Valid States ──────────────────────────────────────────────────

TERMINAL_RUN_STATES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.PASSED,
        RunStatus.FAILED,
        RunStatus.BLOCKED,
        RunStatus.SKIPPED,
        RunStatus.INCONCLUSIVE,
        RunStatus.CANCELLED,
    }
)

VALID_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PASSED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.SKIPPED,
            RunStatus.INCONCLUSIVE,
            RunStatus.CANCELLED,
        }
    ),
    # Terminal states have no valid transitions
    RunStatus.PASSED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.BLOCKED: frozenset(),
    RunStatus.SKIPPED: frozenset(),
    RunStatus.INCONCLUSIVE: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

VALID_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset(
        {
            StepStatus.RUNNING,
            StepStatus.SKIPPED,
            StepStatus.CANCELLED,
        }
    ),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.PASSED,
            StepStatus.FAILED,
            StepStatus.BLOCKED,
            StepStatus.SKIPPED,
            StepStatus.INCONCLUSIVE,
            StepStatus.CANCELLED,
        }
    ),
    # Terminal step states
    StepStatus.PASSED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.BLOCKED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
    StepStatus.INCONCLUSIVE: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}


class RunStateMachine:
    """Validates lifecycle transitions for test runs and individual step attempts."""

    @staticmethod
    def is_terminal(status: RunStatus | str) -> bool:
        """Check if a run status is terminal (cannot transition further)."""
        run_status = RunStatus(status) if isinstance(status, str) else status
        return run_status in TERMINAL_RUN_STATES

    @staticmethod
    def can_transition(
        current_status: RunStatus | str,
        target_status: RunStatus | str,
    ) -> bool:
        """Return True if transitioning from current_status to target_status is valid."""
        curr = RunStatus(current_status) if isinstance(current_status, str) else current_status
        target = RunStatus(target_status) if isinstance(target_status, str) else target_status

        # Self-transition is a no-op / allowed
        if curr == target:
            return True

        allowed = VALID_RUN_TRANSITIONS.get(curr, frozenset())
        return target in allowed

    @classmethod
    def assert_valid_transition(
        cls,
        current_status: RunStatus | str,
        target_status: RunStatus | str,
    ) -> None:
        """Enforce valid run transition or raise InvalidStateTransitionError."""
        curr = RunStatus(current_status) if isinstance(current_status, str) else current_status
        target = RunStatus(target_status) if isinstance(target_status, str) else target_status

        if not cls.can_transition(curr, target):
            raise InvalidStateTransitionError(
                current_state=curr.value,
                target_state=target.value,
                entity="run",
            )

    @staticmethod
    def can_transition_step(
        current_status: StepStatus | str,
        target_status: StepStatus | str,
    ) -> bool:
        """Return True if transitioning step attempt status is valid."""
        curr = StepStatus(current_status) if isinstance(current_status, str) else current_status
        target = StepStatus(target_status) if isinstance(target_status, str) else target_status

        if curr == target:
            return True

        allowed = VALID_STEP_TRANSITIONS.get(curr, frozenset())
        return target in allowed

    @classmethod
    def assert_valid_step_transition(
        cls,
        current_status: StepStatus | str,
        target_status: StepStatus | str,
    ) -> None:
        """Enforce valid step transition or raise InvalidStateTransitionError."""
        curr = StepStatus(current_status) if isinstance(current_status, str) else current_status
        target = StepStatus(target_status) if isinstance(target_status, str) else target_status

        if not cls.can_transition_step(curr, target):
            raise InvalidStateTransitionError(
                current_state=curr.value,
                target_state=target.value,
                entity="step",
            )
