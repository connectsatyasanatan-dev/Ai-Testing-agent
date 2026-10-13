"""Add api_tokens table and worker lease_expires_at column to runs (Tasks P4-00, P3-02).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. api_tokens table
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "token_hash",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), default=False, nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    # 2. Add lease_expires_at to runs
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("lease_expires_at")
    op.drop_table("api_tokens")
