"""Normalize backtest research persistence around versions and workflow runs.

Revision ID: 20260809_19
Revises: 20260808_18
Create Date: 2026-08-09
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260809_19"
down_revision: str | None = "20260808_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = postgresql.JSONB(astext_type=sa.Text())
SUCCESS_STATES = {"SUCCESS", "FORCED_SUCCESS"}
FAILURE_STATES = {"FAILURE", "STOP", "KILL", "SUBMIT_FAILED"}
TERMINAL_STATES = SUCCESS_STATES | FAILURE_STATES


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def parameter_overrides(base: Any, parameters: Any) -> Any:
    if not isinstance(base, Mapping) or not isinstance(parameters, Mapping):
        return parameters
    return {
        name: parameter_overrides(base.get(name), value)
        for name, value in parameters.items()
        if name not in base or base[name] != value
    }


def reset_sequence(table: str) -> None:
    op.execute(
        sa.text(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE(MAX(id), 1),
                MAX(id) IS NOT NULL
            )
            FROM {table}
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "backtest_researches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("analysis_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["backtest_versions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_backtest_researches_version_type_created",
        "backtest_researches",
        ["version_id", "analysis_type", "created_at"],
    )
    op.create_table(
        "backtest_research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("research_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("parameter_overrides", JSON, nullable=False),
        sa.ForeignKeyConstraint(["research_id"], ["backtest_researches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("research_id", "sequence", name="uq_backtest_research_runs_sequence"),
        sa.UniqueConstraint("workflow_run_id", name="uq_backtest_research_runs_workflow_run"),
    )

    connection = op.get_bind()
    researches = connection.execute(
        sa.text(
            """
            SELECT task.id, version.id AS version_id, task.analysis_type, task.created_at
            FROM batch_research_tasks AS task
            JOIN backtest_versions AS version
              ON version.project_id = task.source_project_id
             AND version.version = task.source_version
            WHERE task.application = 'backtest'
            ORDER BY task.id
            """
        )
    ).mappings().all()
    for research in researches:
        connection.execute(
            sa.text(
                """
                INSERT INTO backtest_researches (id, version_id, analysis_type, created_at)
                VALUES (:id, :version_id, :analysis_type, :created_at)
                """
            ),
            dict(research),
        )

    runs = connection.execute(
        sa.text(
            """
            SELECT item.id, item.batch_task_id AS research_id, item.sequence,
                   item.label, item.workflow_run_id, workflow_run.payload,
                   version.parameters AS base_parameters
            FROM batch_research_items AS item
            JOIN batch_research_tasks AS task ON task.id = item.batch_task_id
            JOIN backtest_versions AS version
              ON version.project_id = task.source_project_id
             AND version.version = task.source_version
            JOIN workflow_runs AS workflow_run ON workflow_run.id = item.workflow_run_id
            WHERE task.application = 'backtest'
            ORDER BY item.id
            """
        )
    ).mappings().all()
    for run in runs:
        parameters = json_object(json_object(run["payload"]).get("input_json"))
        overrides = parameter_overrides(json_object(run["base_parameters"]), parameters)
        connection.execute(
            sa.text(
                """
                INSERT INTO backtest_research_runs
                    (id, research_id, sequence, label, workflow_run_id, parameter_overrides)
                VALUES
                    (:id, :research_id, :sequence, :label, :workflow_run_id,
                     CAST(:parameter_overrides AS JSONB))
                """
            ),
            {
                "id": run["id"],
                "research_id": run["research_id"],
                "sequence": run["sequence"],
                "label": run["label"],
                "workflow_run_id": run["workflow_run_id"],
                "parameter_overrides": json.dumps(overrides, ensure_ascii=False),
            },
        )

    reset_sequence("backtest_researches")
    reset_sequence("backtest_research_runs")
    op.drop_table("batch_research_items")
    op.drop_table("batch_research_tasks")


def downgrade() -> None:
    op.create_table(
        "batch_research_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application", sa.String(32), nullable=False),
        sa.Column("analysis_type", sa.String(64), nullable=False),
        sa.Column("source_project_id", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request", JSON, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_batch_research_tasks_user_id", "batch_research_tasks", ["user_id"])
    op.create_index("ix_batch_research_tasks_user_created", "batch_research_tasks", ["user_id", "created_at"])
    op.create_index("ix_batch_research_tasks_source", "batch_research_tasks", ["application", "source_project_id", "source_version"])
    op.create_index("ix_batch_research_tasks_state", "batch_research_tasks", ["state"])
    op.create_table(
        "batch_research_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_task_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("workflow_run_id", sa.Integer()),
        sa.Column("workflow_instance_id", sa.BigInteger()),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("parameters", JSON, nullable=False),
        sa.Column("result", JSON),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["batch_task_id"], ["batch_research_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["workflow_instances.workflow_instance_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("batch_task_id", "sequence", name="uq_batch_research_items_sequence"),
        sa.UniqueConstraint("workflow_run_id", name="uq_batch_research_items_workflow_run"),
    )
    op.create_index("ix_batch_research_items_batch_task_id", "batch_research_items", ["batch_task_id"])
    op.create_index("ix_batch_research_items_task_state", "batch_research_items", ["batch_task_id", "state"])
    op.create_index("ix_batch_research_items_workflow_run_id", "batch_research_items", ["workflow_run_id"])
    op.create_index("ix_batch_research_items_workflow_instance_id", "batch_research_items", ["workflow_instance_id"])
    op.create_index("ix_batch_research_items_state", "batch_research_items", ["state"])

    connection = op.get_bind()
    researches = connection.execute(
        sa.text(
            """
            SELECT research.id, research.analysis_type, research.created_at,
                   version.project_id, version.version, project.user_id
            FROM backtest_researches AS research
            JOIN backtest_versions AS version ON version.id = research.version_id
            JOIN backtest_projects AS project ON project.id = version.project_id
            ORDER BY research.id
            """
        )
    ).mappings().all()
    for research in researches:
        run_rows = connection.execute(
            sa.text(
                """
                SELECT research_run.id, research_run.sequence, research_run.label,
                       workflow_run.id AS workflow_run_id, workflow_run.submission_state,
                       workflow_run.payload, workflow_run.error AS run_error,
                       workflow.workflow_instance_id, workflow.state AS workflow_state,
                       workflow.error AS workflow_error
                FROM backtest_research_runs AS research_run
                JOIN workflow_runs AS workflow_run ON workflow_run.id = research_run.workflow_run_id
                LEFT JOIN workflow_instances AS workflow
                  ON workflow.workflow_run_id = workflow_run.id AND workflow.is_current = true
                WHERE research_run.research_id = :research_id
                ORDER BY research_run.sequence
                """
            ),
            {"research_id": research["id"]},
        ).mappings().all()
        states = [run["workflow_state"] or run["submission_state"] for run in run_rows]
        completed = sum(state in SUCCESS_STATES for state in states)
        failed = sum(state in FAILURE_STATES for state in states)
        if not states or any(state not in TERMINAL_STATES for state in states):
            state = "RUNNING"
        elif failed == 0:
            state = "SUCCESS"
        elif completed == 0:
            state = "FAILURE"
        else:
            state = "PARTIAL_SUCCESS"
        request_items = []
        errors = []
        for run in run_rows:
            payload = json_object(run["payload"])
            parameters = json_object(payload.get("input_json"))
            request_items.append({"label": run["label"], "parameters": parameters})
            error = run["workflow_error"] or run["run_error"]
            if error:
                errors.append(error)
        request = {
            "application": "backtest",
            "analysis_type": research["analysis_type"],
            "project_id": research["project_id"],
            "version": research["version"],
            "items": request_items,
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO batch_research_tasks
                    (id, user_id, application, analysis_type, source_project_id,
                     source_version, state, requested_count, completed_count,
                     failed_count, request, error, created_at, updated_at)
                VALUES
                    (:id, :user_id, 'backtest', :analysis_type, :project_id,
                     :version, :state, :requested_count, :completed_count,
                     :failed_count, CAST(:request AS JSONB), :error,
                     :created_at, :created_at)
                """
            ),
            {
                **dict(research),
                "state": state,
                "requested_count": len(run_rows),
                "completed_count": completed,
                "failed_count": failed,
                "request": json.dumps(request, ensure_ascii=False),
                "error": "; ".join(dict.fromkeys(errors)) or None,
            },
        )
        for run, item in zip(run_rows, request_items, strict=True):
            item_error = run["workflow_error"] or run["run_error"]
            connection.execute(
                sa.text(
                    """
                    INSERT INTO batch_research_items
                        (id, batch_task_id, sequence, label, workflow_run_id,
                         workflow_instance_id, state, parameters, result, error,
                         created_at, updated_at)
                    VALUES
                        (:id, :batch_task_id, :sequence, :label, :workflow_run_id,
                         :workflow_instance_id, :state, CAST(:parameters AS JSONB),
                         NULL, :error, :created_at, :created_at)
                    """
                ),
                {
                    "id": run["id"],
                    "batch_task_id": research["id"],
                    "sequence": run["sequence"],
                    "label": run["label"],
                    "workflow_run_id": run["workflow_run_id"],
                    "workflow_instance_id": run["workflow_instance_id"],
                    "state": run["workflow_state"] or run["submission_state"],
                    "parameters": json.dumps(item["parameters"], ensure_ascii=False),
                    "error": item_error,
                    "created_at": research["created_at"],
                },
            )

    reset_sequence("batch_research_tasks")
    reset_sequence("batch_research_items")
    op.drop_table("backtest_research_runs")
    op.drop_table("backtest_researches")
