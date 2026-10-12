"""
uitp_contracts.run
==================
Schemas for runs, step attempts, and run events.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.enums import RunStatus, StepStatus


class RunSchema(BaseModel):
    """Summary representation of a test run (API response shape)."""

    model_config = ConfigDict(frozen=True)

    id: str
    """UUIDv4 run identifier."""

    plan_id: str
    plan_version: int

    environment_id: str
    project_id: str

    status: RunStatus
    requested_by: str
    """UUIDv4 of the user who requested the run."""

    worker_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class StepAttemptSchema(BaseModel):
    """A single attempt to execute one step."""

    model_config = ConfigDict(frozen=True)

    id: str
    """UUIDv4 attempt identifier."""

    run_id: str
    step_id: str
    attempt_number: int = Field(..., ge=1)

    status: StepStatus
    error_code: str | None = None
    error_message: str | None = None

    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunEventSchema(BaseModel):
    """A structured event emitted during a run (timeline entry)."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    run_id: str
    step_id: str | None = None
    attempt_id: str | None = None

    event_type: str
    """e.g. 'step_started', 'step_passed', 'step_failed', 'run_cancelled'"""

    timestamp: datetime
    message: str | None = None
    metadata: dict[str, str | int | float | bool | None] | None = None


class CreateRunRequest(BaseModel):
    """Request body for POST /api/v1/plans/{plan_id}/runs."""

    environment_id: str
    plan_version: int | None = None
    """Optional exact plan version to execute. If omitted, uses the currently approved version."""
    idempotency_key: str | None = None
    """Optional client-provided key to prevent duplicate run creation (P3-03)."""
    background: bool = False
    """If True, enqueues the run into the worker queue instead of running synchronously."""
