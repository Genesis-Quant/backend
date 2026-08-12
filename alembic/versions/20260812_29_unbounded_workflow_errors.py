"""Store complete batch result errors without a character limit."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_29"
down_revision: str | Sequence[str] | None = "20260810_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "backtest_research_items",
        "result_error",
        existing_type=sa.String(length=4000),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "backtest_research_items",
        "result_error",
        existing_type=sa.Text(),
        type_=sa.String(length=4000),
        existing_nullable=True,
    )
