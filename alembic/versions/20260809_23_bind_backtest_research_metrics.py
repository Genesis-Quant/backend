"""Bind calculated research metrics to a workflow instance.

Revision ID: 20260809_23
Revises: 20260809_22
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_23"
down_revision: str | None = "20260809_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtest_research_runs",
        sa.Column("result_workflow_instance_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backtest_research_runs", "result_workflow_instance_id")
