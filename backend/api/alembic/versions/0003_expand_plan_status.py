"""Expand plan_status enum with pending_review and rejected.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # In PostgreSQL, native enums require ALTER TYPE ADD VALUE.
    # In SQLite, enum columns are text/check constraints.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE plan_status ADD VALUE IF NOT EXISTS 'pending_review'")
        op.execute("ALTER TYPE plan_status ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    pass
