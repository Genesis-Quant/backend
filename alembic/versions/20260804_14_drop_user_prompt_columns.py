"""Drop obsolete assistant prompt columns from users.

Revision ID: 20260804_14
Revises: 20260803_13
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260804_14"
down_revision: str | None = "20260803_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "result_assistant_prompt")
    op.drop_column("users", "factor_assistant_prompt")


def downgrade() -> None:
    for column_name in ("factor_assistant_prompt", "result_assistant_prompt"):
        op.add_column(
            "users",
            sa.Column(column_name, sa.Text(), nullable=False, server_default=sa.text("''")),
        )
        op.alter_column("users", column_name, server_default=None)
