"""Represent editable project drafts as versions.

Revision ID: 20260810_27
Revises: 20260809_26
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260810_27"
down_revision: str | None = "20260809_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    migrate_application("factor", "metrics")
    migrate_application("backtest", "summary")


def migrate_application(application: str, result_column: str) -> None:
    versions = f"{application}_versions"
    projects = f"{application}_projects"
    workspaces = f"{application}_workflow_workspaces"

    op.add_column(versions, sa.Column("workflow_workspace_id", sa.Integer(), comment=f"版本独占的{'因子' if application == 'factor' else '回测'}工作空间主键"))
    op.add_column(versions, sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否已经保存为不可变版本"))
    op.add_column(versions, sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否为项目当前可更新版本"))
    op.add_column(versions, sa.Column("updated_at", sa.DateTime(timezone=True), comment="更新时间"))
    op.alter_column(versions, "workflow_instance_id", existing_type=sa.BigInteger(), nullable=True, comment="已保存结果采用的工作流实例主键")
    op.alter_column(versions, "parameters", existing_type=JSON_VALUE, comment=f"当前版本的{'因子分析' if application == 'factor' else '回测'}请求参数")
    op.alter_column(versions, result_column, existing_type=JSON_VALUE, nullable=True, comment=f"已保存版本的{'因子分析摘要指标' if application == 'factor' else '回测摘要指标'}")
    op.execute(f"UPDATE {versions} SET updated_at = created_at")
    op.alter_column(versions, "updated_at", nullable=False)

    op.execute(
        f"""
        UPDATE {versions} AS version
        SET workflow_workspace_id = attempt.workflow_workspace_id
        FROM workflow_instances AS instance
        JOIN workflow_attempts AS attempt ON attempt.id = instance.workflow_attempt_id
        WHERE instance.workflow_instance_id = version.workflow_instance_id
        """
    )
    op.execute(
        f"""
        WITH draft_workspaces AS (
            SELECT
                workspace.id AS workflow_workspace_id,
                workspace.project_id,
                COALESCE(attempt.input_json, '{{}}'::jsonb) AS parameters,
                base.created_at,
                ROW_NUMBER() OVER (PARTITION BY workspace.project_id ORDER BY workspace.id) AS position
            FROM {workspaces} AS workspace
            JOIN workflow_workspaces AS base ON base.id = workspace.id
            LEFT JOIN workflow_attempts AS attempt
                ON attempt.workflow_workspace_id = workspace.id AND attempt.is_current = true
            WHERE workspace.project_id IS NOT NULL
              AND workspace.saved = false
              AND NOT EXISTS (
                  SELECT 1 FROM {versions} AS version
                  WHERE version.workflow_workspace_id = workspace.id
              )
        ),
        version_numbers AS (
            SELECT project.id AS project_id, COALESCE(MAX(version.version), 0) AS maximum
            FROM {projects} AS project
            LEFT JOIN {versions} AS version ON version.project_id = project.id
            GROUP BY project.id
        )
        INSERT INTO {versions} (
            project_id, workflow_workspace_id, workflow_instance_id, version,
            saved, is_current, remark, parameters, {result_column}, created_at, updated_at
        )
        SELECT
            draft.project_id, draft.workflow_workspace_id, NULL,
            numbers.maximum + draft.position, false, true, '', draft.parameters,
            NULL, draft.created_at, draft.created_at
        FROM draft_workspaces AS draft
        JOIN version_numbers AS numbers ON numbers.project_id = draft.project_id
        """
    )
    op.execute(
        f"""
        WITH missing_projects AS (
            SELECT project.id, project.user_id, project.created_at,
                   COALESCE(MAX(version.version), 0) + 1 AS version
            FROM {projects} AS project
            LEFT JOIN {versions} AS version ON version.project_id = project.id
            GROUP BY project.id, project.user_id, project.created_at
            HAVING NOT BOOL_OR(COALESCE(version.is_current, false))
        ),
        created_workspaces AS (
            INSERT INTO workflow_workspaces (
                user_id, application, workspace_key, source_project_id, created_at
            )
            SELECT
                project.user_id, '{application}',
                md5(random()::text || clock_timestamp()::text || project.id::text),
                project.id, project.created_at
            FROM missing_projects AS project
            RETURNING id, source_project_id, created_at
        ),
        created_application_workspaces AS (
            INSERT INTO {workspaces} (id)
            SELECT id FROM created_workspaces
            RETURNING id
        )
        INSERT INTO {versions} (
            project_id, workflow_workspace_id, workflow_instance_id, version,
            saved, is_current, remark, parameters, {result_column}, created_at, updated_at
        )
        SELECT
            project.id, workspace.id, NULL, project.version,
            false, true, '', '{{}}'::jsonb, NULL,
            workspace.created_at, workspace.created_at
        FROM missing_projects AS project
        JOIN created_workspaces AS workspace ON workspace.source_project_id = project.id
        JOIN created_application_workspaces AS typed_workspace ON typed_workspace.id = workspace.id
        """
    )

    op.alter_column(versions, "workflow_workspace_id", nullable=False)
    op.create_foreign_key(
        f"{versions}_workflow_workspace_id_fkey",
        versions,
        workspaces,
        ["workflow_workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(f"uq_{versions}_workflow_workspace", versions, ["workflow_workspace_id"])
    op.create_check_constraint(f"ck_{versions}_saved_not_current", versions, "NOT (saved AND is_current)")
    op.create_check_constraint(f"ck_{versions}_saved_workflow_instance", versions, "NOT saved OR workflow_instance_id IS NOT NULL")
    op.create_index(
        f"uq_{versions}_current_project",
        versions,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )
    op.alter_column(versions, "saved", server_default=None)
    op.alter_column(versions, "is_current", server_default=None)

    op.drop_index(f"uq_{workspaces}_project_draft", table_name=workspaces)
    op.drop_index(f"ix_{workspaces}_project_id", table_name=workspaces)
    op.drop_constraint(f"{workspaces}_project_id_fkey", workspaces, type_="foreignkey")
    op.drop_column(workspaces, "saved")
    op.drop_column(workspaces, "project_id")


def downgrade() -> None:
    restore_application("backtest", "summary")
    restore_application("factor", "metrics")


def restore_application(application: str, result_column: str) -> None:
    versions = f"{application}_versions"
    projects = f"{application}_projects"
    workspaces = f"{application}_workflow_workspaces"

    op.add_column(workspaces, sa.Column("project_id", sa.Integer(), comment=f"关联{'回测' if application == 'backtest' else '因子'}项目主键"))
    op.add_column(workspaces, sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否已保存为版本"))
    op.execute(
        f"""
        UPDATE {workspaces} AS workspace
        SET project_id = CASE WHEN version.saved OR version.is_current THEN version.project_id ELSE NULL END,
            saved = version.saved
        FROM {versions} AS version
        WHERE version.workflow_workspace_id = workspace.id
        """
    )
    op.create_foreign_key(
        f"{workspaces}_project_id_fkey",
        workspaces,
        projects,
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_{workspaces}_project_id", workspaces, ["project_id"])
    op.create_index(
        f"uq_{workspaces}_project_draft",
        workspaces,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL AND saved = false"),
    )
    op.alter_column(workspaces, "saved", server_default=None)

    op.execute(f"DELETE FROM {versions} WHERE saved = false")
    op.drop_index(f"uq_{versions}_current_project", table_name=versions)
    op.drop_constraint(f"ck_{versions}_saved_workflow_instance", versions, type_="check")
    op.drop_constraint(f"ck_{versions}_saved_not_current", versions, type_="check")
    op.drop_constraint(f"uq_{versions}_workflow_workspace", versions, type_="unique")
    op.drop_constraint(f"{versions}_workflow_workspace_id_fkey", versions, type_="foreignkey")
    op.alter_column(versions, "workflow_instance_id", existing_type=sa.BigInteger(), nullable=False, comment="生成该版本的工作流实例主键")
    op.alter_column(versions, "parameters", existing_type=JSON_VALUE, comment=f"{'因子分析' if application == 'factor' else '回测'}请求参数快照")
    op.alter_column(versions, result_column, existing_type=JSON_VALUE, nullable=False, comment=f"{'因子分析摘要指标' if application == 'factor' else '回测摘要指标'}")
    op.drop_column(versions, "updated_at")
    op.drop_column(versions, "is_current")
    op.drop_column(versions, "saved")
    op.drop_column(versions, "workflow_workspace_id")
