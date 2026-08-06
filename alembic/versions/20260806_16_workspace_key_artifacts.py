"""Replace persisted artifact paths with workspace keys.

Revision ID: 20260806_16
Revises: 20260806_15
Create Date: 2026-08-06
"""

from collections.abc import Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260806_16"
down_revision: str | None = "20260806_15"
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
            WHERE source_project_id IS NULL
              AND EXISTS (
                SELECT 1
                FROM {run_table}
                WHERE {run_table}.id = workflow_runs.id
                  AND {run_table}.project_id IS NOT NULL
            )
        """))
    op.add_column(
        "workflow_runs",
        sa.Column("workspace_key", sa.String(length=32), nullable=True),
    )
    connection = op.get_bind()
    runs = connection.execute(sa.text("""
        SELECT id, application, input_file, output_dir
        FROM workflow_runs
        ORDER BY id
    """)).mappings()
    for run in runs:
        workspace_key = migrated_workspace_key(
            int(run["id"]),
            run["input_file"],
            run["output_dir"],
        )
        connection.execute(
            sa.text("""
                UPDATE workflow_runs
                SET workspace_key = :workspace_key
                WHERE id = :run_id
            """),
            {"workspace_key": workspace_key, "run_id": run["id"]},
        )
    op.alter_column(
        "workflow_runs",
        "workspace_key",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_index(
        "ix_workflow_runs_workspace_key",
        "workflow_runs",
        ["workspace_key"],
        unique=True,
    )
    op.drop_column("workflow_runs", "output_dir")
    op.drop_column("workflow_runs", "input_file")


def downgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("input_file", sa.Text(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("output_dir", sa.Text(), nullable=True),
    )
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE workflow_runs
        SET input_file = '/shared/' || application || '/' || workspace_key || '/input.json',
            output_dir = '/shared/' || application || '/' || workspace_key || '/output'
    """))
    op.drop_index("ix_workflow_runs_workspace_key", table_name="workflow_runs")
    op.drop_column("workflow_runs", "workspace_key")


def migrated_workspace_key(
    run_id: int,
    input_file: object,
    output_dir: object,
) -> str:
    candidates = {
        key
        for value in (input_file, output_dir)
        if (key := workspace_key_from_path(value)) is not None
    }
    if len(candidates) > 1:
        raise RuntimeError(
            f"workflow run {run_id} 的输入输出 workspace 不一致: {sorted(candidates)}"
        )
    if candidates:
        return candidates.pop()
    if input_file or output_dir:
        raise RuntimeError(
            f"无法从 workflow run {run_id} 的历史路径中解析 workspace key: "
            f"{input_file!r}, {output_dir!r}"
        )
    return uuid4().hex


def workspace_key_from_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [part for part in value.strip().replace("\\", "/").split("/") if part]
    if len(parts) < 2 or parts[-1] not in {"input.json", "output"}:
        return None
    candidate = parts[-2].lower()
    if len(candidate) != 32:
        return None
    try:
        parsed = UUID(candidate).hex
    except ValueError:
        return None
    return parsed if parsed == candidate else None
