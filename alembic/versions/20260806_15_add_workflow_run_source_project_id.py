"""Backfill the source project identifier on workflow runs.

Revision ID: 20260806_15
Revises: 20260804_14
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260806_15"
down_revision: str | None = "20260804_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for run_table in (
        "query_workflow_runs",
        "factor_workflow_runs",
        "backtest_workflow_runs",
    ):
        op.execute(sa.text(f"""
            UPDATE workflow_runs
            SET source_project_id = (
                SELECT project_id
                FROM {run_table}
                WHERE {run_table}.id = workflow_runs.id
            )
            WHERE EXISTS (
                SELECT 1
                FROM {run_table}
                WHERE {run_table}.id = workflow_runs.id
                  AND {run_table}.project_id IS NOT NULL
            )
        """))


def downgrade() -> None:
    pass
