"""Store calculated backtest research metrics.

Revision ID: 20260809_22
Revises: 20260809_21
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260809_22"
down_revision: str | None = "20260809_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtest_research_runs", sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("backtest_research_runs", sa.Column("result_error", sa.String(4000), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_research_runs", "result_error")
    op.drop_column("backtest_research_runs", "metrics")
