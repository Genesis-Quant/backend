"""Add workflow task snapshots to application tasks.

Revision ID: 20260803_08
Revises: 20260802_07
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260803_08"
down_revision: str | None = "20260802_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("query_tasks", "factor_tasks", "backtest_tasks")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "workflow_tasks",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_column(table, "workflow_tasks")
