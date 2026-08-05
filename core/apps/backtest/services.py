"""Backtest workflow submission, strategy projects, versions, and results."""

from typing import Any

from fastapi import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.apps.backtest.models import (
    BacktestProject,
    BacktestVersion,
    BacktestWorkflowRun,
)
from core.apps.workflows.models import WorkflowInstance, WorkflowRun, utc_now
from core.apps.workflows.services import (
    WorkflowExecutionService,
    current_workflow_instance,
    remove_run_artifacts,
    resolve_run_directory,
    workflow_input_json,
)
from core.scheduler.domain import TERMINAL_STATES
from core.utils.dsl import build_dsl_catalog
from core.utils.results import result_files, result_response

OUTPUT_FILES = {
    "trade_details": "trade_details.parquet",
    "daily_positions": "daily_positions.parquet",
    "daily_portfolios": "daily_portfolios.parquet",
    "return_summary": "return_summary.parquet",
    "daily_trading_statistics": "daily_trading_statistics.parquet",
    "engine_stat": "engine_stat.parquet",
}
PROJECT_OUTPUTS = [
    "trade_details",
    "daily_positions",
    "daily_portfolios",
    "daily_trading_statistics",
]


def submit_backtest_workflow(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> WorkflowRun:
    return WorkflowExecutionService("backtest", BacktestWorkflowRun).submit(session, user_id, payload, outputs)


def backtest_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, workflow_instance_id, "backtest", OUTPUT_FILES)


def backtest_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(session, user_id, workflow_instance_id, name, "backtest", OUTPUT_FILES)


def list_backtest_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(BacktestProject).where(BacktestProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(statement.order_by(BacktestProject.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serialize_project(session, project) for project in projects], "page": page, "page_size": page_size, "total": total}


def create_backtest_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    project = BacktestProject(user_id=user_id, title=title)
    session.add(project)
    session.commit()
    return serialize_project(session, project)


def get_backtest_project(session: Session, user_id: int, project_id: int) -> dict[str, Any]:
    return serialize_project(session, owned_project(session, user_id, project_id))


def update_backtest_project(session: Session, user_id: int, project_id: int, title: str) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    project.title = title
    project.updated_at = utc_now()
    session.commit()
    return serialize_project(session, project)


def delete_backtest_project(session: Session, user_id: int, project_id: int) -> int:
    project = owned_project(session, user_id, project_id)
    runs = list(session.scalars(select(BacktestWorkflowRun).where(BacktestWorkflowRun.project_id == project.id)))
    running = [run_state(session, run) for run in runs if run_state(session, run) not in TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的回测工作流: {sorted(set(running))}")
    artifacts = [
        (resolve_run_directory(run), run.output_dir)
        for run in runs
        if run.input_file
    ]
    session.execute(delete(BacktestVersion).where(BacktestVersion.project_id == project.id))
    for run in runs:
        session.delete(run)
    session.delete(project)
    session.commit()
    for run_artifacts in artifacts:
        remove_run_artifacts(*run_artifacts)
    return project_id


def submit_project_backtest(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> WorkflowRun:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    draft = session.scalar(select(BacktestWorkflowRun).where(BacktestWorkflowRun.project_id == project.id, BacktestWorkflowRun.saved.is_(False)).with_for_update())
    if draft is not None and run_state(session, draft) not in TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {run_state(session, draft)} 状态的回测工作流")
    if draft is not None:
        draft.project_id = None
        session.flush()
    run = BacktestWorkflowRun(
        user_id=user_id,
        application="backtest",
        source_project_id=project.id,
        project_id=project.id,
        saved=False,
        payload={"start_parameters": {}, "input_json": payload},
        requested_outputs=PROJECT_OUTPUTS,
        submission_state="CREATED",
        events=[],
    )
    session.add(run)
    session.flush()
    WorkflowExecutionService("backtest", BacktestWorkflowRun).submit_run(session, run, create_directory=True)
    project.updated_at = utc_now()
    session.commit()
    return run


def create_backtest_version(session: Session, user_id: int, project_id: int, workflow_instance_id: int, remark: str, summary: dict[str, Any]) -> dict[str, Any]:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    row = session.execute(
        select(BacktestWorkflowRun, WorkflowInstance)
        .join(WorkflowInstance, WorkflowInstance.workflow_run_id == BacktestWorkflowRun.id)
        .where(
            BacktestWorkflowRun.project_id == project.id,
            BacktestWorkflowRun.user_id == user_id,
            BacktestWorkflowRun.saved.is_(False),
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowInstance.is_current.is_(True),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError("当前未保存回测不存在或 workflow_instance_id 已失效")
    run, workflow = row
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流状态为 {workflow.state}，成功后才能保存版本")
    backtest_result_files(session, user_id, workflow_instance_id)
    next_version = (session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id)) or 0) + 1
    version = BacktestVersion(project_id=project.id, workflow_instance_id=workflow.workflow_instance_id, version=next_version, remark=remark, parameters=workflow_input_json(run), summary=summary)
    session.add(version)
    run.saved = True
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(version)


def list_backtest_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    versions = session.scalars(select(BacktestVersion).where(BacktestVersion.project_id == project.id).order_by(BacktestVersion.version.desc())).all()
    return [serialize_version(version) for version in versions]


def get_backtest_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    return serialize_version(version)


def dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> BacktestProject:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    return project


def run_state(session: Session, run: WorkflowRun) -> str:
    workflow = current_workflow_instance(session, run.id)
    return workflow.state if workflow is not None else run.submission_state


def serialize_project(session: Session, project: BacktestProject) -> dict[str, Any]:
    latest = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id).order_by(BacktestVersion.version.desc()).limit(1))
    draft = session.scalar(select(BacktestWorkflowRun).where(BacktestWorkflowRun.project_id == project.id, BacktestWorkflowRun.saved.is_(False)))
    workflow = current_workflow_instance(session, draft.id) if draft is not None else None
    draft_data = None if draft is None else {
        "record_id": draft.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow.state if workflow is not None else draft.submission_state,
        "error": draft.error,
        "parameters": workflow_input_json(draft),
        "updated_at": draft.updated_at,
    }
    return {"id": project.id, "title": project.title, "latest_version": latest.version if latest else None, "latest_summary": latest.summary if latest else None, "draft": draft_data, "created_at": project.created_at, "updated_at": project.updated_at}


def serialize_version(version: BacktestVersion) -> dict[str, Any]:
    return {"id": version.id, "project_id": version.project_id, "workflow_instance_id": version.workflow_instance_id, "version": version.version, "remark": version.remark, "parameters": version.parameters, "summary": version.summary, "created_at": version.created_at}
