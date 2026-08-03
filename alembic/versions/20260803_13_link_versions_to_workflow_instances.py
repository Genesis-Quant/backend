"""Link saved research versions directly to workflow instances.

Revision ID: 20260803_13
Revises: 20260803_12
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_13"
down_revision: str | None = "20260803_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_instances", sa.Column("error", sa.Text()))
    # Revision 12 intentionally cleared legacy versions, so no row conversion is required.
    for application in ("factor", "backtest"):
        table = f"{application}_versions"
        op.drop_constraint(f"fk_{application}_versions_workflow_run", table, type_="foreignkey")
        op.drop_constraint(f"uq_{application}_versions_workflow_run", table, type_="unique")
        op.alter_column(
            table,
            "workflow_run_id",
            new_column_name="workflow_instance_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
        op.create_unique_constraint(
            f"uq_{application}_versions_workflow_instance",
            table,
            ["workflow_instance_id"],
        )
        op.create_foreign_key(
            f"fk_{application}_versions_workflow_instance",
            table,
            "workflow_instances",
            ["workflow_instance_id"],
            ["workflow_instance_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for application in ("factor", "backtest"):
        table = f"{application}_versions"
        op.drop_constraint(f"fk_{application}_versions_workflow_instance", table, type_="foreignkey")
        op.drop_constraint(f"uq_{application}_versions_workflow_instance", table, type_="unique")
        op.add_column(table, sa.Column("workflow_run_id", sa.Integer()))
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS version
                SET workflow_run_id = instance.workflow_run_id
                FROM workflow_instances AS instance
                WHERE instance.workflow_instance_id = version.workflow_instance_id
                """
            )
        )
        op.alter_column(
            table,
            "workflow_run_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.drop_column(table, "workflow_instance_id")
        op.create_unique_constraint(
            f"uq_{application}_versions_workflow_run",
            table,
            ["workflow_run_id"],
        )
        op.create_foreign_key(
            f"fk_{application}_versions_workflow_run",
            table,
            "workflow_runs",
            ["workflow_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.drop_column("workflow_instances", "error")
