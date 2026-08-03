"""Index DolphinScheduler child task instance ownership.

Revision ID: 20260803_11
Revises: 20260803_10
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_11"
down_revision: str | None = "20260803_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_TABLES = {
    "query": "query_tasks",
    "factor": "factor_tasks",
    "backtest": "backtest_tasks",
    "incremental": "incremental_update_tasks",
}


def upgrade() -> None:
    op.create_table(
        "workflow_task_instances",
        sa.Column(
            "task_instance_id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("application", sa.String(length=32), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("task_code", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_workflow_task_instances_parent",
        "workflow_task_instances",
        ["application", "record_id"],
    )

    for application, table in TASK_TABLES.items():
        backfill_snapshot_instances(application, table)
        backfill_current_instances(application, table)
        backfill_historical_instances(application, table)


def downgrade() -> None:
    op.drop_table("workflow_task_instances")


def backfill_snapshot_instances(application: str, table: str) -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO workflow_task_instances (
                task_instance_id,
                application,
                record_id,
                task_code
            )
            SELECT
                (snapshot.item ->> 'task_id')::bigint,
                :application,
                task.id,
                NULLIF(snapshot.item ->> 'task_code', '')::bigint
            FROM {table} AS task
            CROSS JOIN LATERAL jsonb_array_elements(task.workflow_tasks)
                AS snapshot(item)
            WHERE snapshot.item ->> 'task_id' IS NOT NULL
            ON CONFLICT (task_instance_id) DO NOTHING
            """
        ).bindparams(application=application)
    )


def backfill_current_instances(application: str, table: str) -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO workflow_task_instances (
                task_instance_id,
                application,
                record_id,
                task_code
            )
            SELECT task_id, :application, id, NULL
            FROM {table}
            WHERE task_id IS NOT NULL
            ON CONFLICT (task_instance_id) DO NOTHING
            """
        ).bindparams(application=application)
    )


def backfill_historical_instances(application: str, table: str) -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO workflow_task_instances (
                task_instance_id,
                application,
                record_id,
                task_code
            )
            SELECT history.task_id::bigint, :application, task.id, NULL
            FROM {table} AS task
            CROSS JOIN LATERAL jsonb_array_elements_text(task.task_id_history)
                AS history(task_id)
            ON CONFLICT (task_instance_id) DO NOTHING
            """
        ).bindparams(application=application)
    )
