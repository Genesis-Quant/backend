"""Create the schema managed by Arena Backend.

Revision ID: 20260731_01
Revises:
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKEND_SCHEMA = "arena_backend"


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema(BACKEND_SCHEMA, if_not_exists=True))


def downgrade() -> None:
    op.execute(sa.schema.DropSchema(BACKEND_SCHEMA, cascade=True, if_exists=True))
