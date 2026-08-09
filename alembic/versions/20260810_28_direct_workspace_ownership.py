"""Make application records own generic workflow workspaces directly.

Revision ID: 20260810_28
Revises: 20260810_27
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260810_28"
down_revision: str | None = "20260810_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_projects",
        sa.Column("workflow_workspace_id", sa.Integer(), comment="查询项目独占的工作流工作空间主键"),
    )
    op.execute(
        """
        UPDATE query_projects AS project
        SET workflow_workspace_id = workspace.id
        FROM query_workflow_workspaces AS workspace
        WHERE workspace.project_id = project.id
        """
    )
    op.execute(
        """
        WITH created AS (
            INSERT INTO workflow_workspaces (
                user_id, application, workspace_key, source_project_id, created_at
            )
            SELECT
                project.user_id, 'query',
                md5(random()::text || clock_timestamp()::text || project.id::text),
                project.id, project.created_at
            FROM query_projects AS project
            WHERE project.workflow_workspace_id IS NULL
            RETURNING id, source_project_id
        )
        UPDATE query_projects AS project
        SET workflow_workspace_id = created.id
        FROM created
        WHERE project.id = created.source_project_id
        """
    )
    op.alter_column("query_projects", "workflow_workspace_id", nullable=False)
    op.create_foreign_key(
        "fk_query_projects_workflow_workspace",
        "query_projects",
        "workflow_workspaces",
        ["workflow_workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_query_projects_workflow_workspace",
        "query_projects",
        ["workflow_workspace_id"],
    )

    replace_workspace_foreign_key(
        "factor_versions",
        "factor_versions_workflow_workspace_id_fkey",
    )
    replace_workspace_foreign_key(
        "backtest_versions",
        "backtest_versions_workflow_workspace_id_fkey",
    )
    replace_workspace_foreign_key(
        "backtest_research_items",
        "fk_backtest_research_items_workflow_workspace",
    )
    op.alter_column(
        "backtest_research_items",
        "workflow_workspace_id",
        comment="批量研究明细独占的工作流工作空间主键",
    )

    remove_unowned_workspaces()
    op.drop_index("ix_workflow_workspaces_source_project_id", table_name="workflow_workspaces")
    op.drop_column("workflow_workspaces", "source_project_id")
    op.drop_table("query_workflow_workspaces")
    op.drop_table("factor_workflow_workspaces")
    op.drop_table("backtest_workflow_workspaces")
    verify_workspace_ownership()


def replace_workspace_foreign_key(table: str, constraint: str) -> None:
    op.drop_constraint(constraint, table, type_="foreignkey")
    op.create_foreign_key(
        constraint,
        table,
        "workflow_workspaces",
        ["workflow_workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def remove_unowned_workspaces() -> None:
    op.execute(
        """
        DELETE FROM workflow_workspaces AS workspace
        WHERE NOT EXISTS (
              SELECT 1 FROM query_projects AS project
              WHERE project.workflow_workspace_id = workspace.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM factor_versions AS version
              WHERE version.workflow_workspace_id = workspace.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM backtest_versions AS version
              WHERE version.workflow_workspace_id = workspace.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM backtest_research_items AS item
              WHERE item.workflow_workspace_id = workspace.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM incremental_workflow_workspaces AS incremental
              WHERE incremental.id = workspace.id
          )
        """
    )


def verify_workspace_ownership() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT workspace.id
                FROM workflow_workspaces AS workspace
                LEFT JOIN query_projects AS query
                    ON query.workflow_workspace_id = workspace.id
                LEFT JOIN factor_versions AS factor
                    ON factor.workflow_workspace_id = workspace.id
                LEFT JOIN backtest_versions AS backtest
                    ON backtest.workflow_workspace_id = workspace.id
                LEFT JOIN backtest_research_items AS research
                    ON research.workflow_workspace_id = workspace.id
                LEFT JOIN incremental_workflow_workspaces AS incremental
                    ON incremental.id = workspace.id
                WHERE
                    (query.id IS NOT NULL)::int
                    + (factor.id IS NOT NULL)::int
                    + (backtest.id IS NOT NULL)::int
                    + (research.id IS NOT NULL)::int
                    + (incremental.id IS NOT NULL)::int <> 1
                   OR workspace.application <> CASE
                        WHEN query.id IS NOT NULL THEN 'query'
                        WHEN factor.id IS NOT NULL THEN 'factor'
                        WHEN backtest.id IS NOT NULL OR research.id IS NOT NULL THEN 'backtest'
                        WHEN incremental.id IS NOT NULL THEN 'incremental'
                    END
            ) THEN
                RAISE EXCEPTION 'workflow_workspaces ownership is incomplete or inconsistent';
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.add_column(
        "workflow_workspaces",
        sa.Column("source_project_id", sa.Integer(), comment="来源应用项目主键"),
    )
    op.create_index(
        "ix_workflow_workspaces_source_project_id",
        "workflow_workspaces",
        ["source_project_id"],
    )
    create_application_workspace_tables()
    restore_application_workspace_rows()
    restore_source_project_ids()

    restore_workspace_foreign_key(
        "backtest_research_items",
        "fk_backtest_research_items_workflow_workspace",
        "backtest_workflow_workspaces",
    )
    restore_workspace_foreign_key(
        "backtest_versions",
        "backtest_versions_workflow_workspace_id_fkey",
        "backtest_workflow_workspaces",
    )
    restore_workspace_foreign_key(
        "factor_versions",
        "factor_versions_workflow_workspace_id_fkey",
        "factor_workflow_workspaces",
    )
    op.alter_column(
        "backtest_research_items",
        "workflow_workspace_id",
        comment="关联回测工作空间主键",
    )
    op.drop_constraint(
        "uq_query_projects_workflow_workspace",
        "query_projects",
        type_="unique",
    )
    op.drop_constraint(
        "fk_query_projects_workflow_workspace",
        "query_projects",
        type_="foreignkey",
    )
    op.drop_column("query_projects", "workflow_workspace_id")


def create_application_workspace_tables() -> None:
    op.create_table(
        "query_workflow_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, comment="工作流工作空间主键"),
        sa.Column("project_id", sa.Integer(), comment="关联查询项目主键"),
        sa.ForeignKeyConstraint(["id"], ["workflow_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["query_projects.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", name="uq_query_workflow_workspaces_project"),
    )
    op.create_table(
        "factor_workflow_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, comment="工作流工作空间主键"),
        sa.ForeignKeyConstraint(["id"], ["workflow_workspaces.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "backtest_workflow_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, comment="工作流工作空间主键"),
        sa.ForeignKeyConstraint(["id"], ["workflow_workspaces.id"], ondelete="CASCADE"),
    )


def restore_application_workspace_rows() -> None:
    op.execute(
        """
        INSERT INTO query_workflow_workspaces (id, project_id)
        SELECT workflow_workspace_id, id FROM query_projects
        """
    )
    op.execute(
        """
        INSERT INTO factor_workflow_workspaces (id)
        SELECT id FROM workflow_workspaces WHERE application = 'factor'
        """
    )
    op.execute(
        """
        INSERT INTO backtest_workflow_workspaces (id)
        SELECT id FROM workflow_workspaces WHERE application = 'backtest'
        """
    )


def restore_source_project_ids() -> None:
    op.execute(
        """
        UPDATE workflow_workspaces AS workspace
        SET source_project_id = project.id
        FROM query_projects AS project
        WHERE project.workflow_workspace_id = workspace.id
        """
    )
    op.execute(
        """
        UPDATE workflow_workspaces AS workspace
        SET source_project_id = version.project_id
        FROM factor_versions AS version
        WHERE version.workflow_workspace_id = workspace.id
        """
    )
    op.execute(
        """
        UPDATE workflow_workspaces AS workspace
        SET source_project_id = version.project_id
        FROM backtest_versions AS version
        WHERE version.workflow_workspace_id = workspace.id
        """
    )
    op.execute(
        """
        UPDATE workflow_workspaces AS workspace
        SET source_project_id = version.project_id
        FROM backtest_research_items AS item
        JOIN backtest_researches AS research ON research.id = item.research_id
        JOIN backtest_versions AS version ON version.id = research.version_id
        WHERE item.workflow_workspace_id = workspace.id
        """
    )


def restore_workspace_foreign_key(
    table: str,
    constraint: str,
    target: str,
) -> None:
    op.drop_constraint(constraint, table, type_="foreignkey")
    op.create_foreign_key(
        constraint,
        table,
        target,
        ["workflow_workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
