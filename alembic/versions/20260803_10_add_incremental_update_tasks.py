"""Add persistent incremental-update task records.

Revision ID: 20260803_10
Revises: 20260803_09
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260803_10"
down_revision: str | None = "20260803_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "incremental_update_tasks"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.BigInteger()),
        sa.Column("project_code", sa.BigInteger()),
        sa.Column("process_definition_code", sa.BigInteger()),
        sa.Column("process_instance_id", sa.BigInteger()),
        sa.Column("workflow_name", sa.String(128)),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("process_state", sa.String(64)),
        sa.Column("host", sa.String(255)),
        sa.Column("retry_times", sa.Integer()),
        sa.Column("max_retry_times", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("task_id_history", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("process_instance_history", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("workflow_tasks", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("state_history", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("input_file", sa.Text()),
        sa.Column("output_dir", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(f"ix_{TABLE}_user_id", TABLE, ["user_id"])
    op.create_index(f"ix_{TABLE}_state", TABLE, ["state"])
    op.create_index(f"ix_{TABLE}_task_id", TABLE, ["task_id"], unique=True)
    op.create_index(
        f"ix_{TABLE}_process_instance_id",
        TABLE,
        ["process_instance_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table(TABLE)
