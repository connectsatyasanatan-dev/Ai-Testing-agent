"""Run, StepAttempt, and RunEvent models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class Run(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of an approved plan against an environment.

    idempotency_key prevents duplicate runs from concurrent requests (P3-03).
    plan_version is stored denormalised so the exact plan state is immutable.
    """

    __tablename__ = "runs"

    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("test_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    environment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("environments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "queued", "running", "passed", "failed",
            "blocked", "skipped", "inconclusive", "cancelled",
            name="run_status",
        ),
        default="queued",
        nullable=False,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Relationships
    step_attempts: Mapped[list["StepAttempt"]] = relationship(
        "StepAttempt", back_populates="run", cascade="all, delete-orphan", lazy="select"
    )
    events: Mapped[list["RunEvent"]] = relationship(
        "RunEvent", back_populates="run", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Run id={self.id!r} status={self.status!r}>"


class StepAttempt(UUIDPrimaryKeyMixin, Base):
    """One attempt to execute a test step (including retries)."""

    __tablename__ = "step_attempts"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending", "running", "passed", "failed",
            "blocked", "skipped", "inconclusive", "cancelled",
            name="step_status",
        ),
        default="pending",
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationship
    run: Mapped["Run"] = relationship("Run", back_populates="step_attempts")


class RunEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable timeline event emitted during a run.

    Append-only — never update or delete run events.
    """

    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationship
    run: Mapped["Run"] = relationship("Run", back_populates="events")
