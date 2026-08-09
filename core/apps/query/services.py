"""Query projects, workflow submissions, and result access."""

from collections.abc import Sequence
from typing import Any

from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.apps.query.models import QueryProject
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    WorkflowExecutionService,
    WORKSPACE_TERMINAL_STATES,
    create_workflow_attempt,
    current_workflow_attempt,
    remove_workspace_artifacts,
    resolve_workspace_artifacts,
    workflow_attempt_state,
    workflow_workspace_state,
)
from core.utils.results import result_files, result_response
from core.utils.time import utc_now

OUTPUT_FILES = {
    "source_data": "source_data.parquet",
    "computed_data": "computed_data.parquet",
    "filtered_data": "filtered_data.parquet",
    "data": "query.parquet",
}
PROJECT_LIMIT = 5
PROJECT_OUTPUTS = ["data"]


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
    return {"items": project_summaries(session, projects), "page": page, "page_size": page_size, "total": total, "limit": PROJECT_LIMIT}


def create_query_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    total = session.scalar(select(func.count()).select_from(QueryProject).where(QueryProject.user_id == user_id)) or 0
    if total >= PROJECT_LIMIT:
        raise RuntimeError(f"每个用户最多创建 {PROJECT_LIMIT} 个查询项目")
    workspace = WorkflowWorkspace(user_id=user_id, application="query")
    session.add(workspace)
    session.flush()
    project = QueryProject(user_id=user_id, workflow_workspace_id=workspace.id, title=title)
    session.add(project)
    session.commit()
    return serialize_project(session, project)


def get_query_project(session: Session, user_id: int, project_id: int) -> dict[str, Any]:
    return serialize_project(session, owned_project(session, user_id, project_id))


def delete_query_project(session: Session, user_id: int, project_id: int) -> int:
    project = owned_project(session, user_id, project_id)
    run = session.get(WorkflowWorkspace, project.workflow_workspace_id)
    if run is None or run.application != "query":
        raise RuntimeError("查询项目关联的工作流工作空间无效")
    state = workflow_workspace_state(session, run)
    if state != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"项目仍有 {state} 状态的查询工作流")
    artifacts = resolve_workspace_artifacts(run)
    session.delete(project)
    session.flush()
    session.delete(run)
    session.commit()
    remove_workspace_artifacts(*artifacts)
    return project_id


def submit_project_query(
    session: Session,
    user_id: int,
    project_id: int,
    payload: dict[str, Any],
) -> WorkflowWorkspace:
    project = session.scalar(
        select(QueryProject).where(
            QueryProject.id == project_id,
            QueryProject.user_id == user_id,
        ).with_for_update()
    )
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    run = session.scalar(select(WorkflowWorkspace).where(WorkflowWorkspace.id == project.workflow_workspace_id).with_for_update())
    if run is None or run.application != "query":
        raise RuntimeError("查询项目关联的工作流工作空间无效")
    state = workflow_workspace_state(session, run)
    if state != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {state} 状态的查询工作流")
    executor = WorkflowExecutionService("query")
    if state == "DRAFT":
        create_workflow_attempt(session, run, payload, PROJECT_OUTPUTS)
        executor.submit_workspace(session, run, create_directory=True)
    else:
        executor.resubmit_workspace(session, run, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return run


def owned_project(session: Session, user_id: int, project_id: int) -> QueryProject:
    project = session.scalar(select(QueryProject).where(QueryProject.id == project_id, QueryProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"查询项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: QueryProject) -> dict[str, Any]:
    run = session.get(WorkflowWorkspace, project.workflow_workspace_id)
    if run is None or run.application != "query":
        raise RuntimeError("查询项目关联的工作流工作空间无效")
    attempt = current_workflow_attempt(session, run.id)
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id)) if attempt is not None else None
    current = None if attempt is None else {
        "workspace_id": run.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow_attempt_state(attempt, workflow),
        "error": (workflow.error if workflow is not None else None) or attempt.error,
        "parameters": attempt.input_json["dataset_query"],
        "updated_at": max(attempt.updated_at, workflow.updated_at) if workflow is not None else attempt.updated_at,
    }
    return {"id": project.id, "title": project.title, "current": current, "created_at": project.created_at, "updated_at": project.updated_at}


def project_summaries(
    session: Session,
    projects: Sequence[QueryProject],
) -> list[dict[str, Any]]:
    workspace_ids = [project.workflow_workspace_id for project in projects]
    rows = list(session.execute(
        select(WorkflowWorkspace, WorkflowAttempt)
        .join(WorkflowAttempt, WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id)
        .where(WorkflowWorkspace.id.in_(workspace_ids), WorkflowWorkspace.application == "query", WorkflowAttempt.is_current.is_(True))
    )) if workspace_ids else []
    attempts_by_workspace = {attempt.workflow_workspace_id: attempt for _, attempt in rows}
    workflows = list(
        session.scalars(
            select(WorkflowInstance).where(
                WorkflowInstance.workflow_attempt_id.in_([attempt.id for attempt in attempts_by_workspace.values()]),
            )
        )
    ) if attempts_by_workspace else []
    runs_by_workspace = {run.id: run for run, _ in rows}
    workflows_by_attempt = {workflow.workflow_attempt_id: workflow for workflow in workflows}
    return [
        project_summary(
            project,
            runs_by_workspace.get(project.workflow_workspace_id),
            attempts_by_workspace,
            workflows_by_attempt,
        )
        for project in projects
    ]


def project_summary(
    project: QueryProject,
    run: WorkflowWorkspace | None,
    attempts_by_workspace: dict[int, WorkflowAttempt],
    workflows_by_attempt: dict[int, WorkflowInstance],
) -> dict[str, Any]:
    attempt = attempts_by_workspace.get(run.id) if run is not None else None
    workflow = workflows_by_attempt.get(attempt.id) if attempt is not None else None
    current = None if attempt is None else {
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow_attempt_state(attempt, workflow),
    }
    return {"id": project.id, "title": project.title, "current": current, "updated_at": project.updated_at}
