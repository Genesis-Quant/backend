"""Add generic batch research task groups and items.

Revision ID: 20260808_18
Revises: 20260806_17
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260808_18"
down_revision: str | None = "20260806_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "batch_research_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application", sa.String(32), nullable=False),
        sa.Column("analysis_type", sa.String(64), nullable=False),
        sa.Column("source_project_id", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request", JSON, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_batch_research_tasks_user_id", "batch_research_tasks", ["user_id"])
    op.create_index("ix_batch_research_tasks_user_created", "batch_research_tasks", ["user_id", "created_at"])
    op.create_index("ix_batch_research_tasks_source", "batch_research_tasks", ["application", "source_project_id", "source_version"])
    op.create_index("ix_batch_research_tasks_state", "batch_research_tasks", ["state"])

    op.create_table(
        "batch_research_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_task_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("workflow_run_id", sa.Integer()),
        sa.Column("workflow_instance_id", sa.BigInteger()),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("parameters", JSON, nullable=False),
        sa.Column("result", JSON),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["batch_task_id"], ["batch_research_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["workflow_instances.workflow_instance_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("batch_task_id", "sequence", name="uq_batch_research_items_sequence"),
        sa.UniqueConstraint("workflow_run_id", name="uq_batch_research_items_workflow_run"),
    )
    op.create_index("ix_batch_research_items_batch_task_id", "batch_research_items", ["batch_task_id"])
    op.create_index("ix_batch_research_items_task_state", "batch_research_items", ["batch_task_id", "state"])
    op.create_index("ix_batch_research_items_workflow_run_id", "batch_research_items", ["workflow_run_id"])
    op.create_index("ix_batch_research_items_workflow_instance_id", "batch_research_items", ["workflow_instance_id"])
    op.create_index("ix_batch_research_items_state", "batch_research_items", ["state"])


def downgrade() -> None:
    op.drop_table("batch_research_items")
    op.drop_table("batch_research_tasks")
