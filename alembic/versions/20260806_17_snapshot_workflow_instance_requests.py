"""Snapshot each workflow instance request.

Revision ID: 20260806_17
Revises: 20260806_16
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260806_17"
down_revision: str | None = "20260806_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column("payload_snapshot", JSONB, nullable=True),
    )
    op.add_column(
        "workflow_instances",
        sa.Column("requested_outputs_snapshot", JSONB, nullable=True),
    )
    op.execute(sa.text("""
        UPDATE workflow_instances AS instance
        SET payload_snapshot = run.payload,
            requested_outputs_snapshot = run.requested_outputs
        FROM workflow_runs AS run
        WHERE run.id = instance.workflow_run_id
    """))
    op.alter_column(
        "workflow_instances",
        "payload_snapshot",
        existing_type=JSONB,
        nullable=False,
    )
    op.alter_column(
        "workflow_instances",
        "requested_outputs_snapshot",
        existing_type=JSONB,
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("workflow_instances", "requested_outputs_snapshot")
    op.drop_column("workflow_instances", "payload_snapshot")
