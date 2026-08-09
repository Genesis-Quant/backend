"""Separate workflow workspaces, attempts, and scheduler instances.

Revision ID: 20260809_26
Revises: 20260809_25
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260809_26"
down_revision: str | None = "20260809_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.rename_table("workflow_runs", "workflow_workspaces")
    op.rename_table("query_workflow_runs", "query_workflow_workspaces")
    op.rename_table("factor_workflow_runs", "factor_workflow_workspaces")
    op.rename_table("backtest_workflow_runs", "backtest_workflow_workspaces")
    op.rename_table("incremental_workflow_runs", "incremental_workflow_workspaces")
    op.rename_table("backtest_research_runs", "backtest_research_items")
    op.execute("ALTER SEQUENCE workflow_runs_id_seq RENAME TO workflow_workspaces_id_seq")
    op.execute("ALTER SEQUENCE backtest_research_runs_id_seq RENAME TO backtest_research_items_id_seq")
    op.execute("ALTER TABLE workflow_workspaces RENAME CONSTRAINT workflow_runs_pkey TO workflow_workspaces_pkey")
    op.execute("ALTER TABLE workflow_workspaces RENAME CONSTRAINT workflow_runs_user_id_fkey TO workflow_workspaces_user_id_fkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_runs_pkey TO query_workflow_workspaces_pkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_runs_id_fkey TO query_workflow_workspaces_id_fkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_runs_project_id_fkey TO query_workflow_workspaces_project_id_fkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_runs_pkey TO factor_workflow_workspaces_pkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_runs_id_fkey TO factor_workflow_workspaces_id_fkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_runs_project_id_fkey TO factor_workflow_workspaces_project_id_fkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_runs_pkey TO backtest_workflow_workspaces_pkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_runs_id_fkey TO backtest_workflow_workspaces_id_fkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_runs_project_id_fkey TO backtest_workflow_workspaces_project_id_fkey")
    op.execute("ALTER TABLE incremental_workflow_workspaces RENAME CONSTRAINT incremental_workflow_runs_pkey TO incremental_workflow_workspaces_pkey")
    op.execute("ALTER TABLE incremental_workflow_workspaces RENAME CONSTRAINT incremental_workflow_runs_id_fkey TO incremental_workflow_workspaces_id_fkey")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT backtest_research_runs_pkey TO backtest_research_items_pkey")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT backtest_research_runs_research_id_fkey TO backtest_research_items_research_id_fkey")

    op.drop_index("ix_workflow_runs_application_submission", table_name="workflow_workspaces")
    op.drop_index("ix_workflow_runs_submission_id", table_name="workflow_workspaces")
    op.execute("ALTER INDEX ix_workflow_runs_user_created RENAME TO ix_workflow_workspaces_user_created")
    op.execute("ALTER INDEX ix_workflow_runs_source_project_id RENAME TO ix_workflow_workspaces_source_project_id")
    op.execute("ALTER INDEX ix_workflow_runs_workspace_key RENAME TO ix_workflow_workspaces_workspace_key")
    op.create_index("ix_workflow_workspaces_application_created", "workflow_workspaces", ["application", "created_at"])

    op.execute("ALTER INDEX ix_factor_workflow_runs_project_id RENAME TO ix_factor_workflow_workspaces_project_id")
    op.execute("ALTER INDEX uq_factor_workflow_runs_project_draft RENAME TO uq_factor_workflow_workspaces_project_draft")
    op.execute("ALTER INDEX ix_backtest_workflow_runs_project_id RENAME TO ix_backtest_workflow_workspaces_project_id")
    op.execute("ALTER INDEX uq_backtest_workflow_runs_project_draft RENAME TO uq_backtest_workflow_workspaces_project_draft")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT uq_query_workflow_runs_project TO uq_query_workflow_workspaces_project")

    op.execute("ALTER TABLE backtest_research_items RENAME COLUMN workflow_run_id TO workflow_workspace_id")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT uq_backtest_research_runs_workflow_run TO uq_backtest_research_items_workflow_workspace")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT fk_backtest_research_runs_workflow_run TO fk_backtest_research_items_workflow_workspace")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT fk_backtest_research_runs_result_workflow_instance TO fk_backtest_research_items_result_workflow_instance")
    op.execute("ALTER INDEX ix_backtest_research_runs_research_id_id RENAME TO ix_backtest_research_items_research_id_id")

    op.create_table(
        "workflow_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, comment="工作流提交尝试主键"),
        sa.Column("workflow_workspace_id", sa.Integer(), nullable=False, comment="所属工作流工作空间主键"),
        sa.Column("is_current", sa.Boolean(), nullable=False, comment="是否为工作空间当前提交尝试"),
        sa.Column("submission_state", sa.String(64), nullable=False, comment="提交尝试状态"),
        sa.Column("project_code", sa.BigInteger(), comment="DolphinScheduler 项目编码"),
        sa.Column("workflow_definition_code", sa.BigInteger(), comment="DolphinScheduler 工作流定义编码"),
        sa.Column("workflow_name", sa.String(128), comment="工作流定义名称"),
        sa.Column("input_json", JSON_VALUE, nullable=False, comment="提交给应用的请求参数"),
        sa.Column("start_parameters", JSON_VALUE, nullable=False, comment="提交给调度器的启动参数"),
        sa.Column("requested_outputs", JSON_VALUE, nullable=False, comment="请求生成的输出名称"),
        sa.Column("error", sa.Text(), comment="准备或提交错误信息"),
        sa.Column("events", JSON_VALUE, nullable=False, comment="提交尝试业务事件记录"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="更新时间"),
        sa.Column("legacy_workflow_instance_id", sa.BigInteger()),
        sa.ForeignKeyConstraint(["workflow_workspace_id"], ["workflow_workspaces.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_workflow_attempts_current_workspace",
        "workflow_attempts",
        ["workflow_workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_index("ix_workflow_attempts_submission_id", "workflow_attempts", ["submission_state", "id"])
    op.create_index("ix_workflow_attempts_workspace_created", "workflow_attempts", ["workflow_workspace_id", "created_at"])

    op.execute(
        """
        INSERT INTO workflow_attempts (
            workflow_workspace_id, is_current, submission_state, project_code,
            workflow_definition_code, workflow_name, input_json, start_parameters,
            requested_outputs, error, events, created_at, updated_at,
            legacy_workflow_instance_id
        )
        SELECT
            instance.workflow_run_id,
            instance.is_current,
            CASE WHEN instance.is_current THEN workspace.submission_state ELSE 'WORKFLOW_CREATED' END,
            workspace.project_code,
            workspace.workflow_definition_code,
            workspace.workflow_name,
            COALESCE(instance.payload_snapshot->'input_json', workspace.payload->'input_json', '{}'::jsonb),
            COALESCE(instance.payload_snapshot->'start_parameters', workspace.payload->'start_parameters', '{}'::jsonb),
            COALESCE(instance.requested_outputs_snapshot, workspace.requested_outputs, '[]'::jsonb),
            CASE WHEN instance.is_current THEN workspace.error ELSE NULL END,
            CASE WHEN instance.is_current THEN workspace.events ELSE '[]'::jsonb END,
            instance.created_at,
            instance.updated_at,
            instance.workflow_instance_id
        FROM workflow_instances AS instance
        JOIN workflow_workspaces AS workspace ON workspace.id = instance.workflow_run_id
        """
    )
    op.execute(
        """
        INSERT INTO workflow_attempts (
            workflow_workspace_id, is_current, submission_state, project_code,
            workflow_definition_code, workflow_name, input_json, start_parameters,
            requested_outputs, error, events, created_at, updated_at
        )
        SELECT
            workspace.id, true, workspace.submission_state, workspace.project_code,
            workspace.workflow_definition_code, workspace.workflow_name,
            COALESCE(workspace.payload->'input_json', '{}'::jsonb),
            COALESCE(workspace.payload->'start_parameters', '{}'::jsonb),
            workspace.requested_outputs, workspace.error, workspace.events,
            workspace.created_at, workspace.updated_at
        FROM workflow_workspaces AS workspace
        WHERE NOT EXISTS (
            SELECT 1 FROM workflow_instances AS instance
            WHERE instance.workflow_run_id = workspace.id
        )
        """
    )

    op.add_column("workflow_instances", sa.Column("workflow_attempt_id", sa.Integer(), comment="所属工作流提交尝试主键"))
    op.execute(
        """
        UPDATE workflow_instances AS instance
        SET workflow_attempt_id = attempt.id
        FROM workflow_attempts AS attempt
        WHERE attempt.legacy_workflow_instance_id = instance.workflow_instance_id
        """
    )
    op.alter_column("workflow_instances", "workflow_attempt_id", nullable=False)
    op.create_unique_constraint("uq_workflow_instances_workflow_attempt", "workflow_instances", ["workflow_attempt_id"])
    op.create_foreign_key(
        "fk_workflow_instances_workflow_attempt",
        "workflow_instances",
        "workflow_attempts",
        ["workflow_attempt_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("uq_workflow_instances_current_run", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_workflow_run_id", table_name="workflow_instances")
    op.drop_constraint("workflow_instances_workflow_run_id_fkey", "workflow_instances", type_="foreignkey")
    for column in ("workflow_run_id", "is_current", "payload_snapshot", "requested_outputs_snapshot"):
        op.drop_column("workflow_instances", column)
    op.drop_column("workflow_attempts", "legacy_workflow_instance_id")

    for column in (
        "submission_state",
        "project_code",
        "workflow_definition_code",
        "workflow_name",
        "payload",
        "requested_outputs",
        "error",
        "events",
        "updated_at",
    ):
        op.drop_column("workflow_workspaces", column)

    op.alter_column("workflow_workspaces", "id", comment="工作流工作空间主键")
    op.alter_column("workflow_workspaces", "user_id", comment="所属用户主键")
    op.alter_column("workflow_workspaces", "workspace_key", comment="共享文件工作空间唯一标识")
    for table in ("query_workflow_workspaces", "factor_workflow_workspaces", "backtest_workflow_workspaces", "incremental_workflow_workspaces"):
        op.alter_column(table, "id", comment="工作流工作空间主键")
    op.alter_column("backtest_research_items", "id", comment="批量研究项目主键")
    op.alter_column("backtest_research_items", "workflow_workspace_id", comment="关联回测工作空间主键")


def downgrade() -> None:
    op.add_column("workflow_workspaces", sa.Column("submission_state", sa.String(64), comment="工作流提交状态"))
    op.add_column("workflow_workspaces", sa.Column("project_code", sa.BigInteger(), comment="DolphinScheduler 项目编码"))
    op.add_column("workflow_workspaces", sa.Column("workflow_definition_code", sa.BigInteger(), comment="DolphinScheduler 工作流定义编码"))
    op.add_column("workflow_workspaces", sa.Column("workflow_name", sa.String(128), comment="工作流定义名称"))
    op.add_column("workflow_workspaces", sa.Column("payload", JSON_VALUE, comment="当前运行请求及启动参数"))
    op.add_column("workflow_workspaces", sa.Column("requested_outputs", JSON_VALUE, comment="请求生成的输出名称"))
    op.add_column("workflow_workspaces", sa.Column("error", sa.Text(), comment="运行或提交错误信息"))
    op.add_column("workflow_workspaces", sa.Column("events", JSON_VALUE, comment="工作流业务事件记录"))
    op.add_column("workflow_workspaces", sa.Column("updated_at", sa.DateTime(timezone=True), comment="更新时间"))
    op.execute(
        """
        UPDATE workflow_workspaces AS workspace
        SET submission_state = attempt.submission_state,
            project_code = attempt.project_code,
            workflow_definition_code = attempt.workflow_definition_code,
            workflow_name = attempt.workflow_name,
            payload = jsonb_build_object(
                'input_json', attempt.input_json,
                'start_parameters', attempt.start_parameters
            ),
            requested_outputs = attempt.requested_outputs,
            error = attempt.error,
            events = attempt.events,
            updated_at = attempt.updated_at
        FROM workflow_attempts AS attempt
        WHERE attempt.workflow_workspace_id = workspace.id
          AND attempt.is_current = true
        """
    )
    for column in ("submission_state", "payload", "requested_outputs", "events", "updated_at"):
        op.alter_column("workflow_workspaces", column, nullable=False)

    op.add_column("workflow_instances", sa.Column("workflow_run_id", sa.Integer(), comment="所属工作流运行主键"))
    op.add_column("workflow_instances", sa.Column("is_current", sa.Boolean(), comment="是否为当前重试实例"))
    op.add_column("workflow_instances", sa.Column("payload_snapshot", JSON_VALUE, comment="该实例的运行请求快照"))
    op.add_column("workflow_instances", sa.Column("requested_outputs_snapshot", JSON_VALUE, comment="该实例的输出请求快照"))
    op.execute(
        """
        UPDATE workflow_instances AS instance
        SET workflow_run_id = attempt.workflow_workspace_id,
            is_current = attempt.is_current,
            payload_snapshot = jsonb_build_object(
                'input_json', attempt.input_json,
                'start_parameters', attempt.start_parameters
            ),
            requested_outputs_snapshot = attempt.requested_outputs
        FROM workflow_attempts AS attempt
        WHERE attempt.id = instance.workflow_attempt_id
        """
    )
    for column in ("workflow_run_id", "is_current", "payload_snapshot", "requested_outputs_snapshot"):
        op.alter_column("workflow_instances", column, nullable=False)
    op.create_foreign_key(
        "workflow_instances_workflow_run_id_fkey",
        "workflow_instances",
        "workflow_workspaces",
        ["workflow_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_workflow_instances_workflow_run_id", "workflow_instances", ["workflow_run_id"])
    op.create_index(
        "uq_workflow_instances_current_run",
        "workflow_instances",
        ["workflow_run_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )
    op.drop_constraint("fk_workflow_instances_workflow_attempt", "workflow_instances", type_="foreignkey")
    op.drop_constraint("uq_workflow_instances_workflow_attempt", "workflow_instances", type_="unique")
    op.drop_column("workflow_instances", "workflow_attempt_id")
    op.drop_table("workflow_attempts")

    op.drop_index("ix_workflow_workspaces_application_created", table_name="workflow_workspaces")
    op.create_index("ix_workflow_runs_application_submission", "workflow_workspaces", ["application", "submission_state"])
    op.create_index("ix_workflow_runs_submission_id", "workflow_workspaces", ["submission_state", "id"])
    op.execute("ALTER INDEX ix_workflow_workspaces_user_created RENAME TO ix_workflow_runs_user_created")
    op.execute("ALTER INDEX ix_workflow_workspaces_source_project_id RENAME TO ix_workflow_runs_source_project_id")
    op.execute("ALTER INDEX ix_workflow_workspaces_workspace_key RENAME TO ix_workflow_runs_workspace_key")
    op.execute("ALTER INDEX ix_factor_workflow_workspaces_project_id RENAME TO ix_factor_workflow_runs_project_id")
    op.execute("ALTER INDEX uq_factor_workflow_workspaces_project_draft RENAME TO uq_factor_workflow_runs_project_draft")
    op.execute("ALTER INDEX ix_backtest_workflow_workspaces_project_id RENAME TO ix_backtest_workflow_runs_project_id")
    op.execute("ALTER INDEX uq_backtest_workflow_workspaces_project_draft RENAME TO uq_backtest_workflow_runs_project_draft")

    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT uq_query_workflow_workspaces_project TO uq_query_workflow_runs_project")
    op.execute("ALTER TABLE backtest_research_items RENAME COLUMN workflow_workspace_id TO workflow_run_id")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT uq_backtest_research_items_workflow_workspace TO uq_backtest_research_runs_workflow_run")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT fk_backtest_research_items_workflow_workspace TO fk_backtest_research_runs_workflow_run")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT fk_backtest_research_items_result_workflow_instance TO fk_backtest_research_runs_result_workflow_instance")
    op.execute("ALTER INDEX ix_backtest_research_items_research_id_id RENAME TO ix_backtest_research_runs_research_id_id")

    op.execute("ALTER TABLE workflow_workspaces RENAME CONSTRAINT workflow_workspaces_pkey TO workflow_runs_pkey")
    op.execute("ALTER TABLE workflow_workspaces RENAME CONSTRAINT workflow_workspaces_user_id_fkey TO workflow_runs_user_id_fkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_workspaces_pkey TO query_workflow_runs_pkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_workspaces_id_fkey TO query_workflow_runs_id_fkey")
    op.execute("ALTER TABLE query_workflow_workspaces RENAME CONSTRAINT query_workflow_workspaces_project_id_fkey TO query_workflow_runs_project_id_fkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_workspaces_pkey TO factor_workflow_runs_pkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_workspaces_id_fkey TO factor_workflow_runs_id_fkey")
    op.execute("ALTER TABLE factor_workflow_workspaces RENAME CONSTRAINT factor_workflow_workspaces_project_id_fkey TO factor_workflow_runs_project_id_fkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_workspaces_pkey TO backtest_workflow_runs_pkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_workspaces_id_fkey TO backtest_workflow_runs_id_fkey")
    op.execute("ALTER TABLE backtest_workflow_workspaces RENAME CONSTRAINT backtest_workflow_workspaces_project_id_fkey TO backtest_workflow_runs_project_id_fkey")
    op.execute("ALTER TABLE incremental_workflow_workspaces RENAME CONSTRAINT incremental_workflow_workspaces_pkey TO incremental_workflow_runs_pkey")
    op.execute("ALTER TABLE incremental_workflow_workspaces RENAME CONSTRAINT incremental_workflow_workspaces_id_fkey TO incremental_workflow_runs_id_fkey")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT backtest_research_items_pkey TO backtest_research_runs_pkey")
    op.execute("ALTER TABLE backtest_research_items RENAME CONSTRAINT backtest_research_items_research_id_fkey TO backtest_research_runs_research_id_fkey")
    op.execute("ALTER SEQUENCE workflow_workspaces_id_seq RENAME TO workflow_runs_id_seq")
    op.execute("ALTER SEQUENCE backtest_research_items_id_seq RENAME TO backtest_research_runs_id_seq")

    op.rename_table("workflow_workspaces", "workflow_runs")
    op.rename_table("query_workflow_workspaces", "query_workflow_runs")
    op.rename_table("factor_workflow_workspaces", "factor_workflow_runs")
    op.rename_table("backtest_workflow_workspaces", "backtest_workflow_runs")
    op.rename_table("incremental_workflow_workspaces", "incremental_workflow_runs")
    op.rename_table("backtest_research_items", "backtest_research_runs")
