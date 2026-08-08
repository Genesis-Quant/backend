"""Backtest workflow submission, strategy projects, versions, and results."""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from fastapi import Response
from runtime import BacktestParameters
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from core.apps.backtest.models import (
    BacktestResearch,
    BacktestResearchRun,
    BacktestProject,
    BacktestVersion,
    BacktestWorkflowRun,
)
from core.apps.backtest.schemas import (
    BatchAnalysisType,
    BatchResearchCreate,
    BatchResearchItemCreate,
    FeeAnalysisCreate,
)
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowInstance, WorkflowRun
from core.apps.workflows.services import (
    WorkflowExecutionService,
    remove_run_artifacts,
    resolve_run_artifacts,
    workflow_input_json,
    workflow_run_state,
)
from core.scheduler.domain import FAILURE_STATES, TERMINAL_STATES
from core.utils.results import result_files, result_response
from core.utils.time import utc_now

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
PROJECT_SUMMARY_FIELDS = ("totalReturn", "annualReturn", "sharpeRatio", "annualVolatility", "maxDrawdown", "dailyWinningRate")
BATCH_ANALYSIS_LABELS = {"fee_analysis": "手续费分析", "sensitivity": "参数敏感性"}
BATCH_SUCCESS_STATES = frozenset({"SUCCESS", "FORCED_SUCCESS"})
BATCH_OUTPUTS = {"fee_analysis": ("daily_portfolios",), "sensitivity": ("daily_portfolios",)}


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
    return {"items": project_summaries(session, projects), "page": page, "page_size": page_size, "total": total}


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
    runs = list(
        session.scalars(
            select(BacktestWorkflowRun).where(
                or_(
                    BacktestWorkflowRun.project_id == project.id,
                    BacktestWorkflowRun.source_project_id == project.id,
                )
            )
        )
    )
    running = [state for run in runs if (state := workflow_run_state(session, run)) not in TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的回测工作流: {sorted(set(running))}")
    artifacts = [
        resolve_run_artifacts(run)
        for run in runs
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
    state = workflow_run_state(session, draft) if draft is not None else None
    if state is not None and state not in TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {state} 状态的回测工作流")
    executor = WorkflowExecutionService("backtest", BacktestWorkflowRun)
    if draft is None:
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
        executor.submit_run(session, run, create_directory=True)
    else:
        run = executor.resubmit_run(session, draft, payload, PROJECT_OUTPUTS)
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
    rows = session.execute(
        select(BacktestVersion.id, BacktestVersion.version, BacktestVersion.remark, BacktestVersion.created_at)
        .where(BacktestVersion.project_id == project.id)
        .order_by(BacktestVersion.version.desc())
    ).mappings()
    return [dict(row) for row in rows]


def get_backtest_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    return serialize_version(version)


def owned_project(session: Session, user_id: int, project_id: int) -> BacktestProject:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: BacktestProject) -> dict[str, Any]:
    latest_version = session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id))
    draft = session.scalar(select(BacktestWorkflowRun).where(BacktestWorkflowRun.project_id == project.id, BacktestWorkflowRun.saved.is_(False)))
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_run_id == draft.id, WorkflowInstance.is_current.is_(True))) if draft is not None else None
    return project_information(project, latest_version, draft, workflow)


def project_summaries(
    session: Session,
    projects: Sequence[BacktestProject],
) -> list[dict[str, Any]]:
    project_ids = [project.id for project in projects]
    if not project_ids:
        return []
    latest_numbers = (
        select(
            BacktestVersion.project_id,
            func.max(BacktestVersion.version).label("version"),
        )
        .where(BacktestVersion.project_id.in_(project_ids))
        .group_by(BacktestVersion.project_id)
        .subquery()
    )
    latest_versions = session.execute(
        select(
            BacktestVersion.project_id,
            BacktestVersion.version,
            *(BacktestVersion.summary[name].as_float().label(name) for name in PROJECT_SUMMARY_FIELDS),
        ).join(
            latest_numbers,
            and_(BacktestVersion.project_id == latest_numbers.c.project_id, BacktestVersion.version == latest_numbers.c.version),
        )
    ).mappings()
    latest_by_project = {row["project_id"]: (row["version"], {name: row[name] for name in PROJECT_SUMMARY_FIELDS}) for row in latest_versions}
    return [
        {
            "id": project.id,
            "title": project.title,
            "latest_version": latest_by_project[project.id][0] if project.id in latest_by_project else None,
            "latest_summary": latest_by_project[project.id][1] if project.id in latest_by_project else None,
            "updated_at": project.updated_at,
        }
        for project in projects
    ]


def project_information(
    project: BacktestProject,
    latest_version: int | None,
    draft: BacktestWorkflowRun | None,
    workflow: WorkflowInstance | None,
) -> dict[str, Any]:
    draft_data = None if draft is None else {
        "record_id": draft.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow.state if workflow is not None else draft.submission_state,
        "error": draft.error,
        "parameters": workflow_input_json(draft),
        "updated_at": draft.updated_at,
    }
    return {"id": project.id, "title": project.title, "latest_version": latest_version, "draft": draft_data, "created_at": project.created_at, "updated_at": project.updated_at}


def serialize_version(version: BacktestVersion) -> dict[str, Any]:
    return {"id": version.id, "project_id": version.project_id, "workflow_instance_id": version.workflow_instance_id, "version": version.version, "remark": version.remark, "parameters": version.parameters, "summary": version.summary, "created_at": version.created_at}


def create_batch_research(session: Session, user: User, request: BatchResearchCreate) -> dict[str, Any]:
    outputs = batch_outputs(request.analysis_type)
    version = owned_batch_version(session, user, request.project_id, request.version)
    base_parameters = BacktestParameters.model_validate(version.parameters).model_dump(mode="json")
    parameters = [BacktestParameters.model_validate(item.parameters).model_dump(mode="json") for item in request.items]
    research = BacktestResearch(version_id=version.id, analysis_type=request.analysis_type, description=request.description)
    session.add(research)
    session.flush()
    for item_parameters in parameters:
        workflow_run = BacktestWorkflowRun(
            user_id=user.id,
            application="backtest",
            source_project_id=request.project_id,
            payload={"start_parameters": {}, "input_json": item_parameters},
            requested_outputs=outputs,
            submission_state="QUEUED",
            events=[{"event": "BACKTEST_RESEARCH_RUN", "research_id": research.id}],
        )
        session.add(workflow_run)
        session.flush()
        session.add(
            BacktestResearchRun(
                research_id=research.id,
                workflow_run_id=workflow_run.id,
                parameter_overrides=parameter_overrides(base_parameters, item_parameters),
            )
        )
    session.commit()
    return get_batch_research(session, user, research.id)


def create_fee_analysis(session: Session, user: User, project_id: int, version: int, request: FeeAnalysisCreate) -> dict[str, Any]:
    source = owned_batch_version(session, user, project_id, version)
    base = BacktestParameters.model_validate(source.parameters).model_dump(mode="json")
    items = [BatchResearchItemCreate(parameters=with_commission(base, rate)) for rate in request.rates]
    return create_batch_research(
        session,
        user,
        BatchResearchCreate(
            analysis_type=BatchAnalysisType.FEE_ANALYSIS,
            project_id=project_id,
            version=version,
            items=items,
        ),
    )


def list_batch_research(
    session: Session,
    user: User,
    page: int,
    page_size: int,
    *,
    project_id: int | None = None,
    version: int | None = None,
    analysis_type: str | None = None,
) -> dict[str, Any]:
    statement = (
        select(BacktestResearch, BacktestVersion.project_id, BacktestVersion.version)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(BacktestProject.user_id == user.id)
    )
    if project_id is not None:
        statement = statement.where(BacktestVersion.project_id == project_id)
    if version is not None:
        statement = statement.where(BacktestVersion.version == version)
    if analysis_type is not None:
        statement = statement.where(BacktestResearch.analysis_type == analysis_type)
    total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = session.execute(statement.order_by(BacktestResearch.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    executions = batch_execution_rows(session, [research.id for research, _, _ in rows])
    return {
        "items": [serialize_batch_research(research, row_project_id, row_version, executions.get(research.id, [])) for research, row_project_id, row_version in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_batch_research(session: Session, user: User, research_id: int) -> dict[str, Any]:
    row = session.execute(
        select(BacktestResearch, BacktestVersion.project_id, BacktestVersion.version)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(BacktestResearch.id == research_id, BacktestProject.user_id == user.id)
    ).one_or_none()
    if row is None:
        raise FileNotFoundError(f"批量研究不存在: {research_id}")
    research, project_id, version = row
    executions = batch_execution_rows(session, [research.id]).get(research.id, [])
    return serialize_batch_research(research, project_id, version, executions, include_runs=True)


def owned_batch_version(session: Session, user: User, project_id: int, version: int) -> BacktestVersion:
    row = session.scalar(
        select(BacktestVersion)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(BacktestVersion.project_id == project_id, BacktestVersion.version == version, BacktestProject.user_id == user.id)
    )
    if row is None:
        raise FileNotFoundError(f"策略回测版本不存在: {project_id}/v{version}")
    return row


def parameter_overrides(base: Any, parameters: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(parameters, dict):
        return deepcopy(parameters)
    return {
        name: parameter_overrides(base.get(name), value)
        for name, value in parameters.items()
        if name not in base or base[name] != value
    }


def with_commission(parameters: dict[str, Any], rate: float) -> dict[str, Any]:
    result = deepcopy(parameters)
    config = dict(result["config"])
    config["commission"] = rate
    result["config"] = config
    return result


def batch_outputs(analysis_type: str) -> list[str]:
    outputs = BATCH_OUTPUTS.get(analysis_type)
    if outputs is None:
        raise ValueError(f"未配置回测批量分析 {analysis_type} 所需的输出")
    return list(outputs)


def batch_execution_rows(session: Session, research_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    result = {research_id: [] for research_id in research_ids}
    if not research_ids:
        return result
    rows = session.execute(
        select(BacktestResearchRun, WorkflowRun, WorkflowInstance)
        .join(WorkflowRun, WorkflowRun.id == BacktestResearchRun.workflow_run_id)
        .outerjoin(WorkflowInstance, and_(WorkflowInstance.workflow_run_id == WorkflowRun.id, WorkflowInstance.is_current.is_(True)))
        .where(BacktestResearchRun.research_id.in_(research_ids))
        .order_by(BacktestResearchRun.research_id, BacktestResearchRun.id)
    ).all()
    for research_run, workflow_run, workflow in rows:
        result[research_run.research_id].append(
            {
                "id": research_run.id,
                "workflow_run_id": workflow_run.id,
                "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
                "state": workflow.state if workflow is not None else workflow_run.submission_state,
                "parameters": workflow_input_json(workflow_run),
                "error": (workflow.error if workflow is not None else None) or workflow_run.error,
            }
        )
    return result


def serialize_batch_research(
    research: BacktestResearch,
    project_id: int,
    version: int,
    runs: list[dict[str, Any]],
    *,
    include_runs: bool = False,
) -> dict[str, Any]:
    states = [run["state"] for run in runs]
    completed = sum(state in BATCH_SUCCESS_STATES for state in states)
    failed = sum(state in FAILURE_STATES or state == "SUBMIT_FAILED" for state in states)
    if not states or any(state not in TERMINAL_STATES for state in states):
        state = "RUNNING"
    elif failed == 0:
        state = "SUCCESS"
    elif completed == 0:
        state = "FAILURE"
    else:
        state = "PARTIAL_SUCCESS"
    result: dict[str, Any] = {
        "id": research.id,
        "analysis_type": research.analysis_type,
        "analysis_type_label": BATCH_ANALYSIS_LABELS.get(research.analysis_type, research.analysis_type),
        "project_id": project_id,
        "version": version,
        "description": research.description,
        "state": state,
        "requested_count": len(runs),
        "completed_count": completed,
        "failed_count": failed,
        "created_at": research.created_at,
    }
    if include_runs:
        result["error"] = "; ".join(dict.fromkeys(run["error"] for run in runs if run["error"]))[:4000] or None
        result["items"] = runs
    return result
