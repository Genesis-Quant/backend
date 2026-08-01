"""Add execution metadata and history to application tasks.

Revision ID: 20260801_04
Revises: 20260801_03
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260801_04"
down_revision: str | None = "20260801_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKEND_SCHEMA = "arena_backend"
TABLES = ("query_tasks", "factor_tasks", "backtest_tasks")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("process_state", sa.String(64)), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("host", sa.String(255)), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("retry_times", sa.Integer()), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("max_retry_times", sa.Integer()), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("started_at", sa.DateTime(timezone=True)), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("finished_at", sa.DateTime(timezone=True)), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("duration_seconds", sa.Float()), schema=BACKEND_SCHEMA)
        op.add_column(table, sa.Column("last_synced_at", sa.DateTime(timezone=True)), schema=BACKEND_SCHEMA)
        for column in ("task_id_history", "process_instance_history", "state_history", "events"):
            op.add_column(
                table,
                sa.Column(column, postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
                schema=BACKEND_SCHEMA,
            )


def downgrade() -> None:
    for table in reversed(TABLES):
        for column in ("events", "state_history", "process_instance_history", "task_id_history", "last_synced_at", "duration_seconds", "finished_at", "started_at", "max_retry_times", "retry_times", "host", "process_state"):
            op.drop_column(table, column, schema=BACKEND_SCHEMA)
