"""
uitp_api.models.dag
===================
SQLAlchemy models for multi-agent DAG task definitions, task dependencies,
task execution attempts, and shared run context variables.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class DAGTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A distinct task node in a multi-agent plan DAG."""

    __tablename__ = "dag_tasks"

    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Stable lowercase task identifier within the plan",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_role: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    exports: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0, nullable=False)
    requirement_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    __table_args__ = (
        UniqueConstraint("plan_id", "task_id", name="uq_dag_task_plan_task_id"),
    )


class DAGDependency(UUIDPrimaryKeyMixin, Base):
    """Directed edge in the task dependency DAG."""

    __tablename__ = "dag_dependencies"

    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    child_task_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("plan_id", "parent_task_id", "child_task_id", name="uq_dag_dep_edge"),
    )


class DAGTaskAttempt(UUIDPrimaryKeyMixin, Base):
    """Execution attempt of a specific DAG task within a test run."""

    __tablename__ = "dag_task_attempts"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunContext(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shared, database-backed run context variable exchange for DAG tasks."""

    __tablename__ = "run_contexts"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_task_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "key", name="uq_run_context_key"),
    )
