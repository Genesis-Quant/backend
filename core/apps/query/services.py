"""Query projects, workflow submissions, and result access."""

from typing import Any

from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.apps.query.models import QueryProject, QueryWorkflowRun
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowRun, utc_now
from core.apps.workflows.services import (
    WorkflowExecutionService,
    current_workflow_instance,
    remove_run_artifacts,
    resolve_run_artifacts,
    workflow_input_json,
)
from core.scheduler.domain import TERMINAL_STATES
from core.utils.dsl import build_dsl_catalog
from core.utils.results import result_files, result_response

OUTPUT_FILES = {
    "source_data": "source_data.parquet",
    "computed_data": "computed_data.parquet",
    "filtered_data": "filtered_data.parquet",
    "data": "query.parquet",
}
PROJECT_LIMIT = 5
PROJECT_OUTPUTS = ["data"]


def submit_query_workflow(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> WorkflowRun:
    return WorkflowExecutionService("query", QueryWorkflowRun).submit(session, user_id, payload, outputs)


def query_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, workflow_instance_id, "query", OUTPUT_FILES)


def query_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(session, user_id, workflow_instance_id, name, "query", OUTPUT_FILES)


def list_query_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(QueryProject).where(QueryProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(
        statement.order_by(QueryProject.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
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
    run = session.scalar(select(QueryWorkflowRun).where(QueryWorkflowRun.project_id == project.id))
    if run is not None and workflow_state(session, run) not in TERMINAL_STATES:
        raise RuntimeError(f"项目仍有 {workflow_state(session, run)} 状态的查询工作流")
    artifacts = (
        resolve_run_artifacts(run)
        if run is not None
        else None
    )
    if run is not None:
        session.delete(run)
    session.delete(project)
    session.commit()
    if artifacts is not None:
        remove_run_artifacts(*artifacts)
    return project_id


def submit_project_query(
    session: Session,
    user_id: int,
    project_id: int,
    payload: dict[str, Any],
) -> WorkflowRun:
    project = session.scalar(
        select(QueryProject).where(
            QueryProject.id == project_id,
            QueryProject.user_id == user_id,
        ).with_for_update()
    )
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    run = session.scalar(
        select(QueryWorkflowRun).where(QueryWorkflowRun.project_id == project.id).with_for_update()
    )
    if run is not None and workflow_state(session, run) not in TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {workflow_state(session, run)} 状态的查询工作流")
    executor = WorkflowExecutionService("query", QueryWorkflowRun)
    if run is None:
        run = QueryWorkflowRun(
            user_id=user_id,
            application="query",
            source_project_id=project.id,
            project_id=project.id,
            payload={"start_parameters": {}, "input_json": payload},
            requested_outputs=PROJECT_OUTPUTS,
            submission_state="CREATED",
            events=[],
        )
        session.add(run)
        session.flush()
        executor.submit_run(session, run, create_directory=True)
    else:
        executor.resubmit_run(session, run, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return run


def query_dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> QueryProject:
    project = session.scalar(select(QueryProject).where(QueryProject.id == project_id, QueryProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    return project


def workflow_state(session: Session, run: WorkflowRun) -> str:
    workflow = current_workflow_instance(session, run.id)
    return workflow.state if workflow is not None else run.submission_state


def serialize_project(session: Session, project: QueryProject) -> dict[str, Any]:
    run = session.scalar(select(QueryWorkflowRun).where(QueryWorkflowRun.project_id == project.id))
    workflow = current_workflow_instance(session, run.id) if run is not None else None
    current = None if run is None else {
        "record_id": run.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow.state if workflow is not None else run.submission_state,
        "error": run.error,
        "parameters": workflow_input_json(run)["dataset_query"],
        "updated_at": run.updated_at,
    }
    return {"id": project.id, "title": project.title, "current": current, "created_at": project.created_at, "updated_at": project.updated_at}
