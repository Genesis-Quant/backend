"""Create query, factor, and backtest task tables.

Revision ID: 20260801_03
Revises: 20260801_02
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260801_03"
down_revision: str | None = "20260801_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("query_tasks", "factor_tasks", "backtest_tasks")


def upgrade() -> None:
    for table in TABLES:
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.BigInteger(), nullable=True),
            sa.Column("project_code", sa.BigInteger(), nullable=True),
            sa.Column("process_definition_code", sa.BigInteger(), nullable=True),
            sa.Column("process_instance_id", sa.BigInteger(), nullable=True),
            sa.Column("workflow_name", sa.String(128), nullable=True),
            sa.Column("state", sa.String(64), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("requested_outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("input_file", sa.Text(), nullable=True),
            sa.Column("output_dir", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
        for column in ("user_id", "state"):
            op.create_index(f"ix_{table}_{column}", table, [column])
        for column in ("task_id", "process_instance_id"):
            op.create_index(f"ix_{table}_{column}", table, [column], unique=True)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
