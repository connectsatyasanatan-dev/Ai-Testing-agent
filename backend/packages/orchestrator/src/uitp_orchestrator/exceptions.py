"""
uitp_orchestrator.exceptions
============================
Domain exceptions for run orchestration, state transitions, and background queueing.
"""
from __future__ import annotations


class OrchestratorError(Exception):
    """Base exception for all orchestrator errors."""


class InvalidStateTransitionError(OrchestratorError):
    """Raised when an illegal run or step state transition is attempted."""

    def __init__(self, current_state: str, target_state: str, entity: str = "run") -> None:
        super().__init__(
            f"Invalid {entity} state transition: cannot transition from '{current_state}' to '{target_state}'."
        )
        self.current_state = current_state
        self.target_state = target_state
        self.entity = entity


class DuplicateExecutionError(OrchestratorError):
    """Raised when an attempt is made to claim or execute an already-claimed or terminal run."""

    def __init__(self, run_id: str, current_status: str, worker_id: str | None = None) -> None:
        super().__init__(
            f"Duplicate execution blocked for run '{run_id}': status is already '{current_status}'"
            + (f" claimed by worker '{worker_id}'." if worker_id else ".")
        )
        self.run_id = run_id
        self.current_status = current_status
        self.worker_id = worker_id


class QueueError(OrchestratorError):
    """Raised when an operation on the message/job queue fails."""


class QueueUnavailableError(QueueError):
    """Raised when Redis or the RQ queue backend is unreachable."""


class WorkerExecutionError(OrchestratorError):
    """Raised when unhandled worker execution failure occurs."""


class RunTimeoutError(OrchestratorError):
    """Raised when a test run exceeds its maximum allocated execution window."""
