"""Normalize application runs and DolphinScheduler workflow instances.

Revision ID: 20260803_12
Revises: 20260803_11
Create Date: 2026-08-03

Legacy execution records are intentionally discarded. Projects are retained, while
factor/backtest versions are cleared because their result records no longer exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260803_12"
down_revision: str | None = "20260803_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute("DELETE FROM factor_versions")
    op.execute("DELETE FROM backtest_versions")
    replace_version_task_reference("factor_versions", "uq_factor_versions_task_record")
    replace_version_task_reference("backtest_versions", "uq_backtest_versions_task_record")

    op.drop_table("workflow_task_instances")
    for table in ("query_tasks", "factor_tasks", "backtest_tasks", "incremental_update_tasks"):
        op.drop_table(table)

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application", sa.String(32), nullable=False),
        sa.Column("source_project_id", sa.Integer()),
        sa.Column("submission_state", sa.String(64), nullable=False, server_default="CREATED"),
        sa.Column("project_code", sa.BigInteger()),
        sa.Column("workflow_definition_code", sa.BigInteger()),
        sa.Column("workflow_name", sa.String(128)),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("requested_outputs", JSONB, nullable=False),
        sa.Column("input_file", sa.Text()),
        sa.Column("output_dir", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("events", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])
    op.create_index("ix_workflow_runs_application", "workflow_runs", ["application"])
    op.create_index("ix_workflow_runs_source_project_id", "workflow_runs", ["source_project_id"])
    op.create_index("ix_workflow_runs_submission_state", "workflow_runs", ["submission_state"])
    op.create_index("ix_workflow_runs_user_created", "workflow_runs", ["user_id", "created_at"])
    op.create_index("ix_workflow_runs_application_submission", "workflow_runs", ["application", "submission_state"])

    op.create_table(
        "query_workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer()),
        sa.ForeignKeyConstraint(["id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["query_projects.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", name="uq_query_workflow_runs_project"),
    )
    create_research_run_table("factor", "factor_projects")
    create_research_run_table("backtest", "backtest_projects")
    op.create_table(
        "incremental_workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(["id"], ["workflow_runs.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "workflow_instances",
        sa.Column("workflow_instance_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("state_history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_workflow_instances_workflow_run_id", "workflow_instances", ["workflow_run_id"])
    op.create_index("ix_workflow_instances_state", "workflow_instances", ["state"])
    op.create_index("ix_workflow_instances_state_started", "workflow_instances", ["state", "started_at"])
    op.create_index(
        "uq_workflow_instances_current_run",
        "workflow_instances",
        ["workflow_run_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_foreign_key(
        "fk_factor_versions_workflow_run",
        "factor_versions",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_backtest_versions_workflow_run",
        "backtest_versions",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.execute("DELETE FROM factor_versions")
    op.execute("DELETE FROM backtest_versions")
    op.drop_constraint("fk_factor_versions_workflow_run", "factor_versions", type_="foreignkey")
    op.drop_constraint("fk_backtest_versions_workflow_run", "backtest_versions", type_="foreignkey")

    op.drop_table("workflow_instances")
    op.drop_table("incremental_workflow_runs")
    op.drop_table("backtest_workflow_runs")
    op.drop_table("factor_workflow_runs")
    op.drop_table("query_workflow_runs")
    op.drop_table("workflow_runs")

    for table in ("query_tasks", "factor_tasks", "backtest_tasks", "incremental_update_tasks"):
        create_legacy_task_table(table)
    op.add_column("query_tasks", sa.Column("project_id", sa.Integer()))
    op.create_foreign_key("query_tasks_project_id_fkey", "query_tasks", "query_projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("uq_query_tasks_project_id", "query_tasks", ["project_id"], unique=True, postgresql_where=sa.text("project_id IS NOT NULL"))
    add_legacy_research_columns("factor", "factor_projects")
    add_legacy_research_columns("backtest", "backtest_projects")

    op.create_table(
        "workflow_task_instances",
        sa.Column("task_instance_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("application", sa.String(32), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("task_code", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_workflow_task_instances_parent", "workflow_task_instances", ["application", "record_id"])

    restore_version_task_reference("factor_versions", "uq_factor_versions_task_record", "factor_tasks")
    restore_version_task_reference("backtest_versions", "uq_backtest_versions_task_record", "backtest_tasks")


def replace_version_task_reference(table: str, unique_name: str) -> None:
    op.drop_constraint(f"{table}_task_record_id_fkey", table, type_="foreignkey")
    op.drop_constraint(unique_name, table, type_="unique")
    op.alter_column(table, "task_record_id", new_column_name="workflow_run_id")
    op.create_unique_constraint(f"uq_{table}_workflow_run", table, ["workflow_run_id"])


def restore_version_task_reference(table: str, unique_name: str, task_table: str) -> None:
    op.drop_constraint(f"uq_{table}_workflow_run", table, type_="unique")
    op.alter_column(table, "workflow_run_id", new_column_name="task_record_id")
    op.create_unique_constraint(unique_name, table, ["task_record_id"])
    op.create_foreign_key(f"{table}_task_record_id_fkey", table, task_table, ["task_record_id"], ["id"], ondelete="RESTRICT")


def create_research_run_table(application: str, project_table: str) -> None:
    table = f"{application}_workflow_runs"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer()),
        sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], [f"{project_table}.id"], ondelete="SET NULL"),
    )
    op.create_index(f"ix_{table}_project_id", table, ["project_id"])
    op.create_index(f"ix_{table}_saved", table, ["saved"])
    op.create_index(
        f"uq_{table}_project_draft",
        table,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL AND saved = false"),
    )


def create_legacy_task_table(table: str) -> None:
    op.create_table(
        table,
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
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("requested_outputs", JSONB, nullable=False),
        sa.Column("task_id_history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("process_instance_history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("workflow_tasks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("state_history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("events", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("input_file", sa.Text()),
        sa.Column("output_dir", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    op.create_index(f"ix_{table}_state", table, ["state"])
    op.create_index(f"ix_{table}_task_id", table, ["task_id"], unique=True)
    op.create_index(f"ix_{table}_process_instance_id", table, ["process_instance_id"], unique=True)


def add_legacy_research_columns(application: str, project_table: str) -> None:
    table = f"{application}_tasks"
    op.add_column(table, sa.Column("project_id", sa.Integer()))
    op.add_column(table, sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_foreign_key(f"{table}_project_id_fkey", table, project_table, ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index(f"ix_{table}_project_id", table, ["project_id"])
    op.create_index(f"ix_{table}_saved", table, ["saved"])
    op.create_index(f"uq_{table}_project_draft", table, ["project_id"], unique=True, postgresql_where=sa.text("project_id IS NOT NULL AND saved = false"))
