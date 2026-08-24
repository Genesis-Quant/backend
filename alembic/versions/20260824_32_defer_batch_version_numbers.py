"""Assign batch version numbers only after successful result collection.

Revision ID: 20260824_32
Revises: 20260820_31
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260824_32"
down_revision: str | Sequence[str] | None = "20260820_31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    migrate_versions("factor")
    migrate_versions("backtest")


def migrate_versions(application: str) -> None:
    versions = f"{application}_versions"
    op.alter_column(
        versions,
        "version",
        existing_type=sa.Integer(),
        nullable=True,
        comment="已保存版本或当前草稿的项目内递增版本号；待自动保存记录不预占编号",
    )
    connection = op.get_bind()
    connection.execute(sa.text(
        f"UPDATE {versions} SET version = NULL WHERE NOT saved AND NOT is_current"
    ))
    connection.execute(sa.text(
        f"""
        UPDATE {versions} AS draft
        SET version = GREATEST(
            draft.version,
            COALESCE((
                SELECT MAX(saved.version)
                FROM {versions} AS saved
                WHERE saved.project_id = draft.project_id AND saved.saved
            ), 0) + 1
        )
        WHERE draft.is_current
        """
    ))
    op.create_check_constraint(
        f"ck_{versions}_number_assignment",
        versions,
        "(version IS NOT NULL) = (saved OR is_current)",
    )
    op.create_check_constraint(
        f"ck_{versions}_number_positive",
        versions,
        "version IS NULL OR version > 0",
    )


def downgrade() -> None:
    restore_versions("backtest")
    restore_versions("factor")


def restore_versions(application: str) -> None:
    versions = f"{application}_versions"
    op.drop_constraint(
        f"ck_{versions}_number_positive",
        versions,
        type_="check",
    )
    op.drop_constraint(
        f"ck_{versions}_number_assignment",
        versions,
        type_="check",
    )
    connection = op.get_bind()
    connection.execute(sa.text(
        f"""
        WITH pending AS (
            SELECT
                id,
                project_id,
                CAST(ROW_NUMBER() OVER (
                    PARTITION BY project_id
                    ORDER BY id
                ) AS integer) AS position
            FROM {versions}
            WHERE version IS NULL
        ), maxima AS (
            SELECT project_id, COALESCE(MAX(version), 0) AS maximum
            FROM {versions}
            GROUP BY project_id
        )
        UPDATE {versions} AS version
        SET version = maxima.maximum + pending.position
        FROM pending
        JOIN maxima ON maxima.project_id = pending.project_id
        WHERE version.id = pending.id
        """
    ))
    op.alter_column(
        versions,
        "version",
        existing_type=sa.Integer(),
        nullable=False,
        comment="项目内递增版本号",
    )
