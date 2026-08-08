"""Add a description to backtest research.

Revision ID: 20260809_20
Revises: 20260809_19
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_20"
down_revision: str | None = "20260809_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtest_researches",
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
    )
    op.alter_column("backtest_researches", "description", server_default=None)


def downgrade() -> None:
    op.drop_column("backtest_researches", "description")
