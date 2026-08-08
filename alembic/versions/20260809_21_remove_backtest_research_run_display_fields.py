"""Remove derived backtest research run display fields.

Revision ID: 20260809_21
Revises: 20260809_20
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_21"
down_revision: str | None = "20260809_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_backtest_research_runs_sequence",
        "backtest_research_runs",
        type_="unique",
    )
    op.drop_column("backtest_research_runs", "sequence")
    op.drop_column("backtest_research_runs", "label")


def downgrade() -> None:
    op.add_column("backtest_research_runs", sa.Column("sequence", sa.Integer()))
    op.add_column("backtest_research_runs", sa.Column("label", sa.String(128)))
    op.execute(
        sa.text(
            """
            WITH ordered AS (
                SELECT id, row_number() OVER (
                    PARTITION BY research_id ORDER BY id
                ) AS position
                FROM backtest_research_runs
            )
            UPDATE backtest_research_runs AS run
            SET sequence = ordered.position,
                label = '组合 ' || ordered.position
            FROM ordered
            WHERE run.id = ordered.id
            """
        )
    )
    op.alter_column("backtest_research_runs", "sequence", nullable=False)
    op.alter_column("backtest_research_runs", "label", nullable=False)
    op.create_unique_constraint(
        "uq_backtest_research_runs_sequence",
        "backtest_research_runs",
        ["research_id", "sequence"],
    )
