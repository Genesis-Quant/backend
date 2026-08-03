"""Add factor research projects and immutable versions.

Revision ID: 20260801_05
Revises: 20260801_04
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260801_05"
down_revision: str | None = "20260801_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "factor_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_factor_projects_user_id", "factor_projects", ["user_id"])
    op.create_index("ix_factor_projects_user_updated", "factor_projects", ["user_id", "updated_at"])

    op.add_column("factor_tasks", sa.Column("project_id", sa.Integer()))
    op.add_column("factor_tasks", sa.Column("saved", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_foreign_key(
        "fk_factor_tasks_project_id",
        "factor_tasks",
        "factor_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_factor_tasks_project_id", "factor_tasks", ["project_id"])
    op.create_index("ix_factor_tasks_saved", "factor_tasks", ["saved"])
    op.create_index(
        "uq_factor_tasks_project_draft",
        "factor_tasks",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL AND saved = false"),
    )

    op.create_table(
        "factor_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_record_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(512), server_default="", nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["factor_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_record_id"], ["factor_tasks.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "version", name="uq_factor_versions_project_number"),
        sa.UniqueConstraint("task_record_id", name="uq_factor_versions_task_record"),
    )
    op.create_index("ix_factor_versions_project_created", "factor_versions", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("factor_versions")
    op.drop_index("uq_factor_tasks_project_draft", table_name="factor_tasks")
    op.drop_index("ix_factor_tasks_saved", table_name="factor_tasks")
    op.drop_index("ix_factor_tasks_project_id", table_name="factor_tasks")
    op.drop_constraint("fk_factor_tasks_project_id", "factor_tasks", type_="foreignkey")
    op.drop_column("factor_tasks", "saved")
    op.drop_column("factor_tasks", "project_id")
    op.drop_table("factor_projects")
