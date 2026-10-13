"""Add multi-agent DAG tables: dag_tasks, dag_dependencies, dag_task_attempts, run_contexts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. dag_tasks
    op.create_table(
        "dag_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("assigned_role", sa.String(32), nullable=False),
        sa.Column("adapter_type", sa.String(64), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("exports", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="60.0"),
        sa.Column("requirement_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("plan_id", "task_id", name="uq_dag_task_plan_task_id"),
    )
    op.create_index("ix_dag_tasks_plan_id", "dag_tasks", ["plan_id"])
    op.create_index("ix_dag_tasks_task_id", "dag_tasks", ["task_id"])

    # 2. dag_dependencies
    op.create_table(
        "dag_dependencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_task_id", sa.String(64), nullable=False),
        sa.Column("child_task_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("plan_id", "parent_task_id", "child_task_id", name="uq_dag_dep_edge"),
    )
    op.create_index("ix_dag_dependencies_plan_id", "dag_dependencies", ["plan_id"])

    # 3. dag_task_attempts
    op.create_table(
        "dag_task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dag_task_attempts_run_id", "dag_task_attempts", ["run_id"])
    op.create_index("ix_dag_task_attempts_task_id", "dag_task_attempts", ["task_id"])
    op.create_index("ix_dag_task_attempts_status", "dag_task_attempts", ["status"])

    # 4. run_contexts
    op.create_table(
        "run_contexts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_task_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("run_id", "key", name="uq_run_context_key"),
    )
    op.create_index("ix_run_contexts_run_id", "run_contexts", ["run_id"])
    op.create_index("ix_run_contexts_key", "run_contexts", ["key"])


def downgrade() -> None:
    op.drop_table("run_contexts")
    op.drop_table("dag_task_attempts")
    op.drop_table("dag_dependencies")
    op.drop_table("dag_tasks")
