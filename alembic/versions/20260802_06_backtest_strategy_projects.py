"""Add backtest strategy projects and immutable versions.

Revision ID: 20260802_06
Revises: 20260801_05
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260802_06"
down_revision: str | None = "20260801_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_backtest_projects_user_id", "backtest_projects", ["user_id"])
    op.create_index("ix_backtest_projects_user_updated", "backtest_projects", ["user_id", "updated_at"])

    op.add_column("backtest_tasks", sa.Column("project_id", sa.Integer()))
    op.add_column("backtest_tasks", sa.Column("saved", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_foreign_key("fk_backtest_tasks_project_id", "backtest_tasks", "backtest_projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_backtest_tasks_project_id", "backtest_tasks", ["project_id"])
    op.create_index("ix_backtest_tasks_saved", "backtest_tasks", ["saved"])
    op.create_index("uq_backtest_tasks_project_draft", "backtest_tasks", ["project_id"], unique=True, postgresql_where=sa.text("project_id IS NOT NULL AND saved = false"))

    op.create_table(
        "backtest_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_record_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(512), server_default="", nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["backtest_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_record_id"], ["backtest_tasks.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "version", name="uq_backtest_versions_project_number"),
        sa.UniqueConstraint("task_record_id", name="uq_backtest_versions_task_record"),
    )
    op.create_index("ix_backtest_versions_project_created", "backtest_versions", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("backtest_versions")
    op.drop_index("uq_backtest_tasks_project_draft", table_name="backtest_tasks")
    op.drop_index("ix_backtest_tasks_saved", table_name="backtest_tasks")
    op.drop_index("ix_backtest_tasks_project_id", table_name="backtest_tasks")
    op.drop_constraint("fk_backtest_tasks_project_id", "backtest_tasks", type_="foreignkey")
    op.drop_column("backtest_tasks", "saved")
    op.drop_column("backtest_tasks", "project_id")
    op.drop_table("backtest_projects")
