"""Add the administrator role to Arena users.

Revision ID: 20260803_09
Revises: 20260803_08
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_09"
down_revision: str | None = "20260803_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_is_admin", "users", ["is_admin"], unique=False)
    op.execute(
        sa.text(
            """
            UPDATE users
            SET is_admin = true
            WHERE id = (SELECT id FROM users ORDER BY created_at, id LIMIT 1)
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_users_is_admin", table_name="users")
    op.drop_column("users", "is_admin")
