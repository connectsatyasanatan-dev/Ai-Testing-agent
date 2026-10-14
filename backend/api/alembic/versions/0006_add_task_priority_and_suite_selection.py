"""Add task priority, smoke/regression flags, and run suite_type selection.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add priority and smoke/regression flags to dag_tasks
    with op.batch_alter_table("dag_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.String(16), nullable=False, server_default="P1")
        )
        batch_op.add_column(
            sa.Column("is_smoke", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("is_regression", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    # 2. Add suite_type to runs
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("suite_type", sa.String(32), nullable=False, server_default="all")
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("suite_type")

    with op.batch_alter_table("dag_tasks") as batch_op:
        batch_op.drop_column("is_regression")
        batch_op.drop_column("is_smoke")
        batch_op.drop_column("priority")
