"""Add plan_id column to test_plans to group versions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_plans", sa.Column("plan_id", sa.String(36), nullable=True))
    # Backfill plan_id = id for existing rows
    op.execute("UPDATE test_plans SET plan_id = id WHERE plan_id IS NULL")
    with op.batch_alter_table("test_plans") as batch_op:
        batch_op.alter_column("plan_id", nullable=False)
        batch_op.create_index("ix_test_plans_plan_id", ["plan_id"])


def downgrade() -> None:
    with op.batch_alter_table("test_plans") as batch_op:
        batch_op.drop_index("ix_test_plans_plan_id")
        batch_op.drop_column("plan_id")
