"""Query projects, reusable submissions, and result access."""

import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.apps.query.models import QueryProject, QueryTask
from core.apps.tasks.models import utc_now
from core.apps.tasks.services import TaskExecutionService, resolve_task_directory
from core.apps.users.models import User
from core.scheduler.domain import TERMINAL_STATES
from core.utils.dsl import build_dsl_catalog
from core.utils.results import result_files, result_path

OUTPUT_FILES = {
    "source_data": "source_data.parquet",
    "computed_data": "computed_data.parquet",
    "filtered_data": "filtered_data.parquet",
    "data": "query.parquet",
}
PROJECT_LIMIT = 5
PROJECT_OUTPUTS = ["data"]


def submit_query_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> QueryTask:
    return TaskExecutionService("query", QueryTask).submit(session, user_id, payload, outputs)


def query_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, QueryTask, OUTPUT_FILES)


def query_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, QueryTask, OUTPUT_FILES)


def list_query_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(QueryProject).where(QueryProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(statement.order_by(QueryProject.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serialize_project(session, project) for project in projects], "page": page, "page_size": page_size, "total": total, "limit": PROJECT_LIMIT}


def create_query_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    total = session.scalar(select(func.count()).select_from(QueryProject).where(QueryProject.user_id == user_id)) or 0
    if total >= PROJECT_LIMIT:
        raise RuntimeError(f"每个用户最多创建 {PROJECT_LIMIT} 个查询项目")
    project = QueryProject(user_id=user_id, title=title)
    session.add(project)
    session.commit()
    return serialize_project(session, project)


def get_query_project(session: Session, user_id: int, project_id: int) -> dict[str, Any]:
    return serialize_project(session, owned_project(session, user_id, project_id))


def delete_query_project(session: Session, user_id: int, project_id: int) -> int:
    project = owned_project(session, user_id, project_id)
    task = session.scalar(select(QueryTask).where(QueryTask.project_id == project.id))
    if task is not None and task.state not in TERMINAL_STATES:
        raise RuntimeError(f"项目仍有 {task.state} 状态的查询任务")
    task_directory = resolve_task_directory("query", task) if task is not None and task.input_file else None
    if task is not None:
        session.execute(delete(QueryTask).where(QueryTask.id == task.id))
    session.delete(project)
    session.commit()
    if task_directory is not None and task_directory.exists():
        shutil.rmtree(task_directory)
    return project_id


def submit_project_query(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> tuple[QueryTask, bool]:
    project = session.scalar(select(QueryProject).where(QueryProject.id == project_id, QueryProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    task = session.scalar(select(QueryTask).where(QueryTask.project_id == project.id).with_for_update())
    executor = TaskExecutionService("query", QueryTask)
    reused = task is not None
    if task is None:
        created_at = utc_now()
        task = QueryTask(
            user_id=user_id,
            project_id=project.id,
            payload=payload,
            requested_outputs=PROJECT_OUTPUTS,
            state="CREATED",
            task_id_history=[],
            process_instance_history=[],
            state_history=[{"state": "CREATED", "timestamp": created_at.isoformat()}],
            events=[],
        )
        session.add(task)
        session.flush()
        executor.submit_record(session, task, payload, PROJECT_OUTPUTS, create_directory=True)
    else:
        if task.state not in TERMINAL_STATES:
            raise RuntimeError(f"项目已有 {task.state} 状态的查询任务")
        executor.resubmit(session, task, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return task, reused


def query_dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> QueryProject:
    project = session.scalar(select(QueryProject).where(QueryProject.id == project_id, QueryProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: QueryProject) -> dict[str, Any]:
    task = session.scalar(select(QueryTask).where(QueryTask.project_id == project.id))
    current = None if task is None else {
        "record_id": task.id,
        "task_id": task.task_id,
        "state": task.state,
        "error": task.error,
        "parameters": task.payload["dataset_query"],
        "updated_at": task.updated_at,
    }
    return {"id": project.id, "title": project.title, "current": current, "created_at": project.created_at, "updated_at": project.updated_at}
