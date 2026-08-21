"""Add backtest parameter optimization reports.

Revision ID: 20260815_30
Revises: 20260812_29
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260815_30"
down_revision: str | Sequence[str] | None = "20260812_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_optimizations",
        sa.Column("id", sa.Integer(), primary_key=True, comment="参数调优报告主键"),
        sa.Column("version_id", sa.Integer(), nullable=False, comment="来源回测版本主键"),
        sa.Column("workflow_workspace_id", sa.Integer(), nullable=False, comment="参数调优报告独占的工作流工作空间主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间"),
        sa.ForeignKeyConstraint(["version_id"], ["backtest_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_workspace_id"], ["workflow_workspaces.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("workflow_workspace_id", name="uq_backtest_optimizations_workflow_workspace"),
    )
    op.create_index(
        "ix_backtest_optimizations_version_created",
        "backtest_optimizations",
        ["version_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backtest_optimizations_version_created",
        table_name="backtest_optimizations",
    )
    op.drop_table("backtest_optimizations")
