"""Factor workflow submission, research projects, versions, and results."""

from typing import Any

from fastapi import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.apps.factor.models import FactorProject, FactorVersion, FactorWorkflowRun
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
    "processed_data": "factor_processed.parquet",
    "information_coefficient": "factor_information_coefficients.parquet",
    "group_returns": "factor_group_returns.parquet",
}
PROJECT_OUTPUTS = ["information_coefficient", "group_returns"]


def submit_factor_workflow(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> WorkflowRun:
    return WorkflowExecutionService("factor", FactorWorkflowRun).submit(session, user_id, payload, outputs)


def factor_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, workflow_instance_id, "factor", OUTPUT_FILES)


def factor_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(session, user_id, workflow_instance_id, name, "factor", OUTPUT_FILES)


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
    runs = list(session.scalars(select(FactorWorkflowRun).where(FactorWorkflowRun.project_id == project.id)))
    running = [run_state(session, run) for run in runs if run_state(session, run) not in TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的因子工作流: {sorted(set(running))}")
    artifacts = [
        (resolve_run_directory(run), run.output_dir)
        for run in runs
        if run.input_file
    ]
    session.execute(delete(FactorVersion).where(FactorVersion.project_id == project.id))
    for run in runs:
        session.delete(run)
    session.delete(project)
    session.commit()
    for run_artifacts in artifacts:
        remove_run_artifacts(*run_artifacts)
    return project_id


def submit_project_analysis(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> WorkflowRun:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    draft = session.scalar(select(FactorWorkflowRun).where(FactorWorkflowRun.project_id == project.id, FactorWorkflowRun.saved.is_(False)).with_for_update())
    if draft is not None and run_state(session, draft) not in TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {run_state(session, draft)} 状态的因子工作流")
    if draft is not None:
        draft.project_id = None
        session.flush()
    run = FactorWorkflowRun(
        user_id=user_id,
        application="factor",
        project_id=project.id,
        saved=False,
        payload={"start_parameters": {}, "input_json": payload},
        requested_outputs=PROJECT_OUTPUTS,
        submission_state="CREATED",
        events=[],
    )
    session.add(run)
    session.flush()
    WorkflowExecutionService("factor", FactorWorkflowRun).submit_run(session, run, create_directory=True)
    project.updated_at = utc_now()
    session.commit()
    return run


def create_factor_version(session: Session, user_id: int, project_id: int, workflow_instance_id: int, remark: str, metrics: dict[str, Any]) -> dict[str, Any]:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    row = session.execute(
        select(FactorWorkflowRun, WorkflowInstance)
        .join(WorkflowInstance, WorkflowInstance.workflow_run_id == FactorWorkflowRun.id)
        .where(
            FactorWorkflowRun.project_id == project.id,
            FactorWorkflowRun.user_id == user_id,
            FactorWorkflowRun.saved.is_(False),
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowInstance.is_current.is_(True),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError("当前未保存分析不存在或 workflow_instance_id 已失效")
    run, workflow = row
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流状态为 {workflow.state}，成功后才能保存版本")
    factor_result_files(session, user_id, workflow_instance_id)
    parameters = workflow_input_json(run)
    validate_metric_dimensions(parameters, metrics)
    next_version = (session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id)) or 0) + 1
    version = FactorVersion(project_id=project.id, workflow_instance_id=workflow.workflow_instance_id, version=next_version, remark=remark, parameters=parameters, metrics=metrics)
    session.add(version)
    run.saved = True
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(version)


def list_factor_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    versions = session.scalars(select(FactorVersion).where(FactorVersion.project_id == project.id).order_by(FactorVersion.version.desc())).all()
    return [serialize_version(version) for version in versions]


def get_factor_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"因子版本不存在: {version_number}")
    return serialize_version(version)


def dsl_catalog() -> dict[str, Any]:
    return build_dsl_catalog()


def owned_project(session: Session, user_id: int, project_id: int) -> FactorProject:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    return project


def run_state(session: Session, run: WorkflowRun) -> str:
    workflow = current_workflow_instance(session, run.id)
    return workflow.state if workflow is not None else run.submission_state


def serialize_project(session: Session, project: FactorProject) -> dict[str, Any]:
    latest = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id).order_by(FactorVersion.version.desc()).limit(1))
    draft = session.scalar(select(FactorWorkflowRun).where(FactorWorkflowRun.project_id == project.id, FactorWorkflowRun.saved.is_(False)))
    workflow = current_workflow_instance(session, draft.id) if draft is not None else None
    draft_data = None if draft is None else {
        "record_id": draft.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow.state if workflow is not None else draft.submission_state,
        "error": draft.error,
        "parameters": workflow_input_json(draft),
        "updated_at": draft.updated_at,
    }
    return {"id": project.id, "title": project.title, "latest_version": latest.version if latest else None, "latest_metrics": latest.metrics if latest else None, "draft": draft_data, "created_at": project.created_at, "updated_at": project.updated_at}


def serialize_version(version: FactorVersion) -> dict[str, Any]:
    return {"id": version.id, "project_id": version.project_id, "workflow_instance_id": version.workflow_instance_id, "version": version.version, "remark": version.remark, "parameters": version.parameters, "metrics": version.metrics, "created_at": version.created_at}


def validate_metric_dimensions(parameters: dict[str, Any], metrics: dict[str, Any]) -> None:
    factors = set(parameters.get("factor_columns") or [])
    returns = set(parameters.get("return_columns") or [])
    if set(metrics) != factors:
        raise ValueError(f"metrics 因子必须与 factor_columns 一致: {sorted(factors)}")
    for factor, values in metrics.items():
        if set(values) != returns:
            raise ValueError(f"metrics[{factor!r}] 收益列必须与 return_columns 一致: {sorted(returns)}")
