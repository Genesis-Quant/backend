"""Factor task submission, research projects, versions, and result access."""

import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.apps.factor.models import FactorProject, FactorTask, FactorVersion
from core.apps.tasks.models import utc_now
from core.apps.tasks.services import TaskExecutionService, delete_workflow_task_mappings, resolve_task_directory
from core.utils.dsl import build_dsl_catalog
from core.utils.results import result_files, result_path
from core.scheduler.domain import TERMINAL_STATES

OUTPUT_FILES = {
    "processed_data": "factor_processed.parquet",
    "information_coefficient": "factor_information_coefficients.parquet",
    "group_returns": "factor_group_returns.parquet",
}
PROJECT_OUTPUTS = list(OUTPUT_FILES)


def submit_factor_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> FactorTask:
    return TaskExecutionService("factor", FactorTask).submit(session, user_id, payload, outputs)


def factor_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, FactorTask, OUTPUT_FILES)


def factor_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, FactorTask, OUTPUT_FILES)


def list_factor_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(FactorProject).where(FactorProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(statement.order_by(FactorProject.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serialize_project(session, project) for project in projects], "page": page, "page_size": page_size, "total": total}


def create_factor_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    project = FactorProject(user_id=user_id, title=title)
    session.add(project)
    session.commit()
    return serialize_project(session, project)


def get_factor_project(session: Session, user_id: int, project_id: int) -> dict[str, Any]:
    return serialize_project(session, owned_project(session, user_id, project_id))


def update_factor_project(session: Session, user_id: int, project_id: int, title: str) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    project.title = title
    project.updated_at = utc_now()
    session.commit()
    return serialize_project(session, project)


def delete_factor_project(session: Session, user_id: int, project_id: int) -> int:
    project = owned_project(session, user_id, project_id)
    tasks = list(session.scalars(select(FactorTask).where(FactorTask.project_id == project.id)))
    running = [task.state for task in tasks if task.state not in TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的分析任务: {sorted(set(running))}")
    task_directories = [resolve_task_directory("factor", task) for task in tasks]
    delete_workflow_task_mappings(session, "factor", [task.id for task in tasks])
    session.execute(delete(FactorVersion).where(FactorVersion.project_id == project.id))
    session.execute(delete(FactorTask).where(FactorTask.project_id == project.id))
    session.delete(project)
    session.commit()
    for task_directory in task_directories:
        if task_directory.exists():
            shutil.rmtree(task_directory)
    return project_id


def submit_project_analysis(
    session: Session,
    user_id: int,
    project_id: int,
    payload: dict[str, Any],
) -> tuple[FactorTask, bool]:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    draft = session.scalar(select(FactorTask).where(FactorTask.project_id == project.id, FactorTask.saved.is_(False)).with_for_update())
    executor = TaskExecutionService("factor", FactorTask)
    reused = draft is not None
    if draft is None:
        created_at = utc_now()
        draft = FactorTask(
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
            raise RuntimeError(f"项目已有 {draft.state} 状态的分析任务")
        task = executor.resubmit(session, draft, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return task, reused


def create_factor_version(
    session: Session,
    user_id: int,
    project_id: int,
    task_id: int,
    remark: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    task = session.scalar(select(FactorTask).where(
        FactorTask.project_id == project.id,
        FactorTask.user_id == user_id,
        FactorTask.task_id == task_id,
        FactorTask.saved.is_(False),
    ).with_for_update())
    if task is None:
        raise FileNotFoundError("当前未保存分析不存在或 task_id 已失效")
    if task.state != "SUCCESS":
        raise RuntimeError(f"任务状态为 {task.state}，成功后才能保存版本")
    factor_result_files(session, user_id, task_id)
    validate_metric_dimensions(task.payload, metrics)
    next_version = (session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id)) or 0) + 1
    version = FactorVersion(
        project_id=project.id,
        task_record_id=task.id,
        version=next_version,
        remark=remark,
        parameters=task.payload,
        metrics=metrics,
    )
    session.add(version)
    task.saved = True
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(task, version)


def list_factor_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    versions = session.scalars(select(FactorVersion).where(FactorVersion.project_id == project.id).order_by(FactorVersion.version.desc())).all()
    return [serialize_version(session.get(FactorTask, version.task_record_id), version) for version in versions]


def get_factor_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"因子版本不存在: {version_number}")
    task = session.get(FactorTask, version.task_record_id)
    if task is None or task.task_id is None:
        raise RuntimeError("版本关联的分析任务不完整")
    return serialize_version(task, version)


def dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> FactorProject:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: FactorProject) -> dict[str, Any]:
    latest = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id).order_by(FactorVersion.version.desc()).limit(1))
    draft = session.scalar(select(FactorTask).where(FactorTask.project_id == project.id, FactorTask.saved.is_(False)))
    draft_data = None if draft is None else {
        "record_id": draft.id,
        "task_id": draft.task_id,
        "state": draft.state,
        "error": draft.error,
        "parameters": draft.payload,
        "updated_at": draft.updated_at,
    }
    return {
        "id": project.id,
        "title": project.title,
        "latest_version": latest.version if latest else None,
        "latest_metrics": latest.metrics if latest else None,
        "draft": draft_data,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def serialize_version(task: FactorTask | None, version: FactorVersion) -> dict[str, Any]:
    if task is None or task.task_id is None:
        raise RuntimeError("版本关联的分析任务不完整")
    return {
        "id": version.id,
        "project_id": version.project_id,
        "task_id": task.task_id,
        "version": version.version,
        "remark": version.remark,
        "parameters": version.parameters,
        "metrics": version.metrics,
        "created_at": version.created_at,
    }


def validate_metric_dimensions(parameters: dict[str, Any], metrics: dict[str, Any]) -> None:
    factors = set(parameters.get("factor_columns") or [])
    returns = set(parameters.get("return_columns") or [])
    if set(metrics) != factors:
        raise ValueError(f"metrics 因子必须与 factor_columns 一致: {sorted(factors)}")
    for factor, values in metrics.items():
        if set(values) != returns:
            raise ValueError(f"metrics[{factor!r}] 收益列必须与 return_columns 一致: {sorted(returns)}")
