"""Add reusable query projects.

Revision ID: 20260802_07
Revises: 20260802_06
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_07"
down_revision: str | None = "20260802_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_query_projects_user_id", "query_projects", ["user_id"])
    op.create_index("ix_query_projects_user_updated", "query_projects", ["user_id", "updated_at"])
    op.add_column("query_tasks", sa.Column("project_id", sa.Integer()))
    op.create_foreign_key("fk_query_tasks_project_id", "query_tasks", "query_projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("uq_query_tasks_project_id", "query_tasks", ["project_id"], unique=True, postgresql_where=sa.text("project_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_query_tasks_project_id", table_name="query_tasks")
    op.drop_constraint("fk_query_tasks_project_id", "query_tasks", type_="foreignkey")
    op.drop_column("query_tasks", "project_id")
    op.drop_table("query_projects")
