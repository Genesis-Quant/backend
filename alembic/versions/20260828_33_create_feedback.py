"""Create authenticated user feedback storage.

Revision ID: 20260828_33
Revises: 20260824_32
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260828_33"
down_revision: str | Sequence[str] | None = "20260824_32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, comment="反馈主键"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="提交反馈的用户主键"),
        sa.Column("source", sa.String(length=16), nullable=False, comment="反馈来源：web 或 mcp"),
        sa.Column("content", sa.Text(), nullable=False, comment="反馈正文"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="提交时间"),
        sa.CheckConstraint("source IN ('web', 'mcp')", name="ck_feedback_source"),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 4000",
            name="ck_feedback_content_length",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        comment="用户通过网页或 MCP 提交的反馈",
    )
    op.create_index(
        "ix_feedback_user_created",
        "feedback",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_user_created", table_name="feedback")
    op.drop_table("feedback")
