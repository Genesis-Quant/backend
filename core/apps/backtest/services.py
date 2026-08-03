"""Backtest task submission, strategy projects, versions, and result access."""

import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.apps.backtest.models import BacktestProject, BacktestTask, BacktestVersion
from core.apps.tasks.models import utc_now
from core.apps.tasks.services import TaskExecutionService, resolve_task_directory
from core.scheduler.domain import TERMINAL_STATES
from core.utils.dsl import build_dsl_catalog
from core.utils.results import result_files, result_path

OUTPUT_FILES = {
    "trade_details": "trade_details.parquet",
    "daily_positions": "daily_positions.parquet",
    "daily_portfolios": "daily_portfolios.parquet",
    "return_summary": "return_summary.parquet",
    "daily_trading_statistics": "daily_trading_statistics.parquet",
    "engine_stat": "engine_stat.parquet",
}
PROJECT_OUTPUTS = list(OUTPUT_FILES)


def submit_backtest_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> BacktestTask:
    return TaskExecutionService("backtest", BacktestTask).submit(session, user_id, payload, outputs)


def backtest_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, BacktestTask, OUTPUT_FILES)


def backtest_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, BacktestTask, OUTPUT_FILES)


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
    tasks = list(session.scalars(select(BacktestTask).where(BacktestTask.project_id == project.id)))
    running = [task.state for task in tasks if task.state not in TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的回测任务: {sorted(set(running))}")
    task_directories = [resolve_task_directory("backtest", task) for task in tasks]
    session.execute(delete(BacktestVersion).where(BacktestVersion.project_id == project.id))
    session.execute(delete(BacktestTask).where(BacktestTask.project_id == project.id))
    session.delete(project)
    session.commit()
    for task_directory in task_directories:
        if task_directory.exists():
            shutil.rmtree(task_directory)
    return project_id


def submit_project_backtest(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> tuple[BacktestTask, bool]:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    draft = session.scalar(select(BacktestTask).where(BacktestTask.project_id == project.id, BacktestTask.saved.is_(False)).with_for_update())
    executor = TaskExecutionService("backtest", BacktestTask)
    reused = draft is not None
    if draft is None:
        created_at = utc_now()
        draft = BacktestTask(
            user_id=user_id,
            project_id=project.id,
            saved=False,
            payload=payload,
            requested_outputs=PROJECT_OUTPUTS,
            state="CREATED",
            task_id_history=[],
            process_instance_history=[],
            state_history=[{"state": "CREATED", "timestamp": created_at.isoformat()}],
            events=[],
        )
        session.add(draft)
        session.flush()
        task = executor.submit_record(session, draft, payload, PROJECT_OUTPUTS, create_directory=True)
    else:
        if draft.state not in TERMINAL_STATES:
            raise RuntimeError(f"项目已有 {draft.state} 状态的回测任务")
        task = executor.resubmit(session, draft, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return task, reused


def create_backtest_version(session: Session, user_id: int, project_id: int, task_id: int, remark: str, summary: dict[str, Any]) -> dict[str, Any]:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    task = session.scalar(select(BacktestTask).where(
        BacktestTask.project_id == project.id,
        BacktestTask.user_id == user_id,
        BacktestTask.task_id == task_id,
        BacktestTask.saved.is_(False),
    ).with_for_update())
    if task is None:
        raise FileNotFoundError("当前未保存回测不存在或 task_id 已失效")
    if task.state != "SUCCESS":
        raise RuntimeError(f"任务状态为 {task.state}，成功后才能保存版本")
    backtest_result_files(session, user_id, task_id)
    next_version = (session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id)) or 0) + 1
    version = BacktestVersion(project_id=project.id, task_record_id=task.id, version=next_version, remark=remark, parameters=task.payload, summary=summary)
    session.add(version)
    task.saved = True
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(task, version)


def list_backtest_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    versions = session.scalars(select(BacktestVersion).where(BacktestVersion.project_id == project.id).order_by(BacktestVersion.version.desc())).all()
    return [serialize_version(session.get(BacktestTask, version.task_record_id), version) for version in versions]


def get_backtest_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    return serialize_version(session.get(BacktestTask, version.task_record_id), version)


def dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> BacktestProject:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: BacktestProject) -> dict[str, Any]:
    latest = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id).order_by(BacktestVersion.version.desc()).limit(1))
    draft = session.scalar(select(BacktestTask).where(BacktestTask.project_id == project.id, BacktestTask.saved.is_(False)))
    return {
        "id": project.id,
        "title": project.title,
        "latest_version": latest.version if latest else None,
        "latest_summary": latest.summary if latest else None,
        "draft": None if draft is None else {
            "record_id": draft.id,
            "task_id": draft.task_id,
            "state": draft.state,
            "error": draft.error,
            "parameters": draft.payload,
            "updated_at": draft.updated_at,
        },
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def serialize_version(task: BacktestTask | None, version: BacktestVersion) -> dict[str, Any]:
    if task is None or task.task_id is None:
        raise RuntimeError("版本关联的回测任务不完整")
    return {
        "id": version.id,
        "project_id": version.project_id,
        "task_id": task.task_id,
        "version": version.version,
        "remark": version.remark,
        "parameters": version.parameters,
        "summary": version.summary,
        "created_at": version.created_at,
    }
