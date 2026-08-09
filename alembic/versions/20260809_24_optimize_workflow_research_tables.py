"""Optimize workflow and backtest research table integrity.

Revision ID: 20260809_24
Revises: 20260809_23
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260809_24"
down_revision: str | None = "20260809_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "backtest_research_runs_workflow_run_id_fkey",
        "backtest_research_runs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_backtest_research_runs_workflow_run",
        "backtest_research_runs",
        "backtest_workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_backtest_research_runs_result_workflow_instance",
        "backtest_research_runs",
        "workflow_instances",
        ["result_workflow_instance_id"],
        ["workflow_instance_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_backtest_research_runs_research_id_id",
        "backtest_research_runs",
        ["research_id", "id"],
    )

    op.create_index(
        "ix_workflow_runs_submission_id",
        "workflow_runs",
        ["submission_state", "id"],
    )
    op.create_index(
        "ix_workflow_instances_created_id",
        "workflow_instances",
        ["created_at", "workflow_instance_id"],
    )

    op.drop_index("ix_backtest_projects_user_id", table_name="backtest_projects")
    op.drop_index("ix_factor_projects_user_id", table_name="factor_projects")
    op.drop_index("ix_query_projects_user_id", table_name="query_projects")
    op.drop_index("ix_workflow_runs_user_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_application", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_submission_state", table_name="workflow_runs")
    op.drop_index("ix_workflow_instances_state", table_name="workflow_instances")
    op.drop_index("ix_backtest_workflow_runs_saved", table_name="backtest_workflow_runs")
    op.drop_index("ix_factor_workflow_runs_saved", table_name="factor_workflow_runs")
    op.drop_index("ix_backtest_versions_project_created", table_name="backtest_versions")
    op.drop_index("ix_factor_versions_project_created", table_name="factor_versions")


def downgrade() -> None:
    op.create_index("ix_factor_versions_project_created", "factor_versions", ["project_id", "created_at"])
    op.create_index("ix_backtest_versions_project_created", "backtest_versions", ["project_id", "created_at"])
    op.create_index("ix_factor_workflow_runs_saved", "factor_workflow_runs", ["saved"])
    op.create_index("ix_backtest_workflow_runs_saved", "backtest_workflow_runs", ["saved"])
    op.create_index("ix_workflow_instances_state", "workflow_instances", ["state"])
    op.create_index("ix_workflow_runs_submission_state", "workflow_runs", ["submission_state"])
    op.create_index("ix_workflow_runs_application", "workflow_runs", ["application"])
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])
    op.create_index("ix_query_projects_user_id", "query_projects", ["user_id"])
    op.create_index("ix_factor_projects_user_id", "factor_projects", ["user_id"])
    op.create_index("ix_backtest_projects_user_id", "backtest_projects", ["user_id"])

    op.drop_index("ix_workflow_instances_created_id", table_name="workflow_instances")
    op.drop_index("ix_workflow_runs_submission_id", table_name="workflow_runs")
    op.drop_index(
        "ix_backtest_research_runs_research_id_id",
        table_name="backtest_research_runs",
    )
    op.drop_constraint(
        "fk_backtest_research_runs_result_workflow_instance",
        "backtest_research_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_backtest_research_runs_workflow_run",
        "backtest_research_runs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "backtest_research_runs_workflow_run_id_fkey",
        "backtest_research_runs",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
