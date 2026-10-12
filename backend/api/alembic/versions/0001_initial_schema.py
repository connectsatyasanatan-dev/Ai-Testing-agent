"""Initial schema — all core tables.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── projects ───────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Enum("active", "archived", name="project_status"), nullable=False, server_default="active"),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_status", "projects", ["status"])

    # ── project_memberships ────────────────────────────────────────────────
    op.create_table(
        "project_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Enum("owner", "maintainer", "viewer", name="project_role"), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_user"),
    )
    op.create_index("ix_project_memberships_project_id", "project_memberships", ["project_id"])
    op.create_index("ix_project_memberships_user_id", "project_memberships", ["user_id"])

    # ── environments ───────────────────────────────────────────────────────
    op.create_table(
        "environments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("allowlist", sa.JSON, nullable=True),
        sa.Column("secret_refs", sa.JSON, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.Enum("active", "archived", name="environment_status"), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_environments_project_id", "environments", ["project_id"])

    # ── test_plans ─────────────────────────────────────────────────────────
    op.create_table(
        "test_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("adapter_type", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum("draft", "approved", "archived", name="plan_status"), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("requires_approval", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_test_plans_project_id", "test_plans", ["project_id"])
    op.create_index("ix_test_plans_status", "test_plans", ["status"])

    # ── test_steps ─────────────────────────────────────────────────────────
    op.create_table(
        "test_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(128), nullable=False),
        sa.Column("step_order", sa.Integer, nullable=False),
        sa.Column("adapter_type", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("inputs", sa.JSON, nullable=True),
        sa.Column("assertions", sa.JSON, nullable=True),
        sa.Column("timeout_seconds", sa.Float, nullable=False, server_default="30.0"),
        sa.Column("retry_policy", sa.JSON, nullable=True),
        sa.Column("idempotency_class", sa.Enum("safe", "idempotent", "non_idempotent", "unknown", name="idempotency_class"), nullable=False, server_default="unknown"),
        sa.Column("risk_level", sa.Enum("low", "medium", "high", "critical", name="risk_level"), nullable=False, server_default="low"),
        sa.Column("description", sa.Text, nullable=True),
    )
    op.create_index("ix_test_steps_plan_id", "test_steps", ["plan_id"])

    # ── runs ───────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("test_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_version", sa.Integer, nullable=False),
        sa.Column("environment_id", sa.String(36), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Enum("queued", "running", "passed", "failed", "blocked", "skipped", "inconclusive", "cancelled", name="run_status"), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True, unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_runs_plan_id", "runs", ["plan_id"])
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_status", "runs", ["status"])

    # ── step_attempts ──────────────────────────────────────────────────────
    op.create_table(
        "step_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(128), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.Enum("pending", "running", "passed", "failed", "blocked", "skipped", "inconclusive", "cancelled", name="step_status"), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_step_attempts_run_id", "step_attempts", ["run_id"])

    # ── run_events ─────────────────────────────────────────────────────────
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(128), nullable=True),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("event_metadata", sa.JSON, nullable=True),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_event_type", "run_events", ["event_type"])

    # ── evidence_artifacts ─────────────────────────────────────────────────
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_attempt_id", sa.String(36), sa.ForeignKey("step_attempts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.Enum("screenshot", "trace", "request_log", "response_log", "console_log", "har", name="evidence_type"), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("redaction_status", sa.Enum("pending", "redacted", "failed", "not_required", name="redaction_status"), nullable=False, server_default="pending"),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evidence_artifacts_run_id", "evidence_artifacts", ["run_id"])

    # ── audit_events ───────────────────────────────────────────────────────
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.Enum(
            "user_login", "user_login_failed", "role_change",
            "project_created", "project_archived",
            "environment_created", "environment_modified",
            "secret_reference_updated",
            "plan_created", "plan_approved", "plan_archived",
            "run_created", "run_cancelled",
            "policy_blocked", "artifact_accessed",
            name="audit_event_type",
        ), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("metadata_redacted", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])

    # ── adapter_registrations ──────────────────────────────────────────────
    op.create_table(
        "adapter_registrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("adapter_type", sa.String(64), nullable=False, unique=True),
        sa.Column("adapter_version", sa.String(32), nullable=False),
        sa.Column("capability_document", sa.JSON, nullable=True),
        sa.Column("status", sa.Enum("active", "disabled", name="adapter_status"), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_adapter_registrations_adapter_type", "adapter_registrations", ["adapter_type"], unique=True)


def downgrade() -> None:
    op.drop_table("adapter_registrations")
    op.drop_table("audit_events")
    op.drop_table("evidence_artifacts")
    op.drop_table("run_events")
    op.drop_table("step_attempts")
    op.drop_table("runs")
    op.drop_table("test_steps")
    op.drop_table("test_plans")
    op.drop_table("environments")
    op.drop_table("project_memberships")
    op.drop_table("projects")
    op.drop_table("users")

    # Drop custom enum types (PostgreSQL only)
    for enum in [
        "project_status", "project_role", "environment_status",
        "plan_status", "idempotency_class", "risk_level",
        "run_status", "step_status", "evidence_type",
        "redaction_status", "audit_event_type", "adapter_status",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum}")
