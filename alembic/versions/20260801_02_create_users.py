"""Create the Arena users table.

Revision ID: 20260801_02
Revises: 20260731_01
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260801_02"
down_revision: str | None = "20260731_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKEND_SCHEMA = "arena_backend"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        schema=BACKEND_SCHEMA,
    )
    op.create_index("ix_arena_backend_users_username", "users", ["username"], unique=True, schema=BACKEND_SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_arena_backend_users_username", table_name="users", schema=BACKEND_SCHEMA)
    op.drop_table("users", schema=BACKEND_SCHEMA)
