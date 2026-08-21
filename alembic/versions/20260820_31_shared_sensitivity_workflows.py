"""Run each sensitivity study in one shared-data workflow.

Revision ID: 20260820_31
Revises: 20260815_30
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_31"
down_revision: str | Sequence[str] | None = "20260815_30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "CREATE TEMPORARY TABLE legacy_backtest_research_workspaces ON COMMIT DROP AS "
        "SELECT DISTINCT workflow_workspace_id FROM backtest_research_items"
    ))
    op.drop_table("backtest_research_items")
    connection.execute(sa.text("DELETE FROM backtest_researches"))
    connection.execute(sa.text(
        "DELETE FROM workflow_workspaces WHERE id IN "
        "(SELECT workflow_workspace_id FROM legacy_backtest_research_workspaces)"
    ))
    op.add_column(
        "backtest_researches",
        sa.Column(
            "workflow_workspace_id",
            sa.Integer(),
            nullable=False,
            comment="敏感性分析独占的工作流工作空间主键",
        ),
    )
    op.add_column(
        "backtest_researches",
        sa.Column(
            "result_workflow_instance_id",
            sa.BigInteger(),
            nullable=True,
            comment="已完成结果汇总对应的工作流实例主键",
        ),
    )
    op.add_column(
        "backtest_researches",
        sa.Column(
            "completed_count",
            sa.Integer(),
            nullable=True,
            comment="结果文件中执行成功的参数组合数量",
        ),
    )
    op.add_column(
        "backtest_researches",
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=True,
            comment="结果文件中执行失败的参数组合数量",
        ),
    )
    op.add_column(
        "backtest_researches",
        sa.Column(
            "result_error",
            sa.Text(),
            nullable=True,
            comment="结果文件收集或校验错误",
        ),
    )
    op.create_foreign_key(
        "fk_backtest_researches_workflow_workspace",
        "backtest_researches",
        "workflow_workspaces",
        ["workflow_workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_backtest_researches_result_workflow_instance",
        "backtest_researches",
        "workflow_instances",
        ["result_workflow_instance_id"],
        ["workflow_instance_id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_backtest_researches_workflow_workspace",
        "backtest_researches",
        ["workflow_workspace_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM backtest_researches"))
    op.drop_constraint(
        "fk_backtest_researches_result_workflow_instance",
        "backtest_researches",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_backtest_researches_workflow_workspace",
        "backtest_researches",
        type_="unique",
    )
    op.drop_constraint(
        "fk_backtest_researches_workflow_workspace",
        "backtest_researches",
        type_="foreignkey",
    )
    op.drop_column("backtest_researches", "workflow_workspace_id")
    op.drop_column("backtest_researches", "result_error")
    op.drop_column("backtest_researches", "failed_count")
    op.drop_column("backtest_researches", "completed_count")
    op.drop_column("backtest_researches", "result_workflow_instance_id")
    op.create_table(
        "backtest_research_items",
        sa.Column("id", sa.Integer(), primary_key=True, comment="批量研究项目主键"),
        sa.Column("research_id", sa.Integer(), nullable=False, comment="所属批量研究主键"),
        sa.Column("workflow_workspace_id", sa.Integer(), nullable=False, comment="批量研究明细独占的工作流工作空间主键"),
        sa.Column("parameter_overrides", sa.JSON(), nullable=False, comment="相对来源版本的参数变更"),
        sa.Column("result_workflow_instance_id", sa.BigInteger(), nullable=True, comment="生成当前指标的工作流实例主键"),
        sa.Column("metrics", sa.JSON(), nullable=True, comment="批量研究运行结果指标"),
        sa.Column("result_error", sa.Text(), nullable=True, comment="结果指标生成错误"),
        sa.ForeignKeyConstraint(["research_id"], ["backtest_researches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_workspace_id"], ["workflow_workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_workflow_instance_id"], ["workflow_instances.workflow_instance_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workflow_workspace_id", name="uq_backtest_research_items_workflow_workspace"),
    )
    op.create_index(
        "ix_backtest_research_items_research_id_id",
        "backtest_research_items",
        ["research_id", "id"],
    )
