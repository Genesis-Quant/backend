"""Backtest workflow submission, strategy projects, versions, and results."""

import logging
from collections.abc import Sequence
from copy import deepcopy
from math import isfinite
from typing import Any

from fastapi import Response
import numpy as np
import pandas as pd
from runtime import (
    BacktestParameters,
    OptimizationAlgorithm,
    OptimizationParameters,
    OptimizationSettings,
    SensitivityParameters,
)
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from core.apps.backtest.models import (
    BacktestOptimization,
    BacktestResearch,
    BacktestProject,
    BacktestVersion,
)
from core.apps.schemas import BatchRunItem
from core.apps.backtest.schemas import (
    BatchAnalysisType,
    BatchResearchCreate,
    FeeAnalysisCreate,
)
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    WORKSPACE_FAILURE_STATES,
    WORKSPACE_TERMINAL_STATES,
    WorkflowExecutionService,
    auto_save_metadata,
    auto_save_workspaces,
    create_workflow_attempt,
    current_workflow_attempt,
    finalize_auto_save_workspaces_now,
    record_event,
    remove_workspace_artifacts,
    resolve_workspace_artifacts,
    require_current_workflow_attempt,
    workflow_attempt_state,
    workflow_workspace_state,
)
from core.utils.results import result_dataframe, result_files, result_response
from core.utils.time import utc_now

LOGGER = logging.getLogger(__name__)
OUTPUT_FILES = {
    "trade_details": "trade_details.parquet",
    "daily_positions": "daily_positions.parquet",
    "daily_portfolios": "daily_portfolios.parquet",
    "return_summary": "return_summary.parquet",
    "daily_trading_statistics": "daily_trading_statistics.parquet",
    "engine_stat": "engine_stat.parquet",
}
OPTIONAL_OUTPUTS = {
    "daily_trading_statistics": (
        "当前 DolphinDB Backtest 插件不支持每日交易统计接口，"
        "本次运行未生成 daily_trading_statistics.parquet"
    ),
}
OPTIMIZATION_OUTPUT_FILES = {
    algorithm.value: f"{algorithm.value}.parquet"
    for algorithm in OptimizationAlgorithm
}
SENSITIVITY_OUTPUT_FILES = {"results": "results.parquet"}
PROJECT_OUTPUTS = [
    "trade_details",
    "daily_positions",
    "daily_portfolios",
    "daily_trading_statistics",
]
PROJECT_SUMMARY_FIELDS = ("totalReturn", "annualReturn", "sharpeRatio", "annualVolatility", "maxDrawdown", "dailyWinningRate")
BATCH_ANALYSIS_LABELS = {"fee_analysis": "手续费分析", "sensitivity": "参数敏感性"}
BATCH_SUCCESS_STATES = frozenset({"SUCCESS"})
BATCH_OUTPUTS = ["results"]


def backtest_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(
        session,
        user_id,
        workflow_instance_id,
        "backtest",
        OUTPUT_FILES,
        OPTIONAL_OUTPUTS,
    )


def backtest_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(
        session,
        user_id,
        workflow_instance_id,
        name,
        "backtest",
        OUTPUT_FILES,
        OPTIONAL_OUTPUTS,
    )


def optimization_result_files(session: Session, user_id: int, optimization_id: int) -> list[dict[str, Any]]:
    workflow_instance_id = optimization_workflow_instance_id(session, user_id, optimization_id)
    return result_files(session, user_id, workflow_instance_id, "optimization", OPTIMIZATION_OUTPUT_FILES)


def optimization_result_response(session: Session, user_id: int, optimization_id: int, name: str) -> Response:
    workflow_instance_id = optimization_workflow_instance_id(session, user_id, optimization_id)
    return result_response(session, user_id, workflow_instance_id, name, "optimization", OPTIMIZATION_OUTPUT_FILES)


def sensitivity_result_files(session: Session, user_id: int, research_id: int) -> list[dict[str, Any]]:
    workflow_instance_id = sensitivity_workflow_instance_id(session, user_id, research_id)
    return result_files(session, user_id, workflow_instance_id, "sensitivity", SENSITIVITY_OUTPUT_FILES)


def sensitivity_result_response(session: Session, user_id: int, research_id: int, name: str) -> Response:
    workflow_instance_id = sensitivity_workflow_instance_id(session, user_id, research_id)
    return result_response(session, user_id, workflow_instance_id, name, "sensitivity", SENSITIVITY_OUTPUT_FILES)


def list_backtest_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(BacktestProject).where(BacktestProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(statement.order_by(BacktestProject.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": project_summaries(session, projects), "page": page, "page_size": page_size, "total": total}


def create_backtest_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    project = BacktestProject(user_id=user_id, title=title)
    session.add(project)
    session.flush()
    create_backtest_draft(session, project, user_id, {})
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
    version_workspace_ids = select(BacktestVersion.workflow_workspace_id).where(BacktestVersion.project_id == project.id)
    research_workspace_ids = (
        select(BacktestResearch.workflow_workspace_id)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .where(BacktestVersion.project_id == project.id)
    )
    optimization_workspace_ids = (
        select(BacktestOptimization.workflow_workspace_id)
        .join(BacktestVersion, BacktestVersion.id == BacktestOptimization.version_id)
        .where(BacktestVersion.project_id == project.id)
    )
    owned_workspace_ids = version_workspace_ids.union_all(research_workspace_ids).union_all(optimization_workspace_ids)
    runs = list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.id.in_(owned_workspace_ids))))
    running = [state for run in runs if (state := workflow_workspace_state(session, run)) != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的回测工作流: {sorted(set(running))}")
    artifacts = [
        resolve_workspace_artifacts(run)
        for run in runs
    ]
    session.delete(project)
    session.flush()
    for run in runs:
        session.delete(run)
    session.commit()
    for run_artifacts in artifacts:
        remove_workspace_artifacts(*run_artifacts)
    return project_id


def submit_project_backtest(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> WorkflowWorkspace:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.is_current.is_(True)).with_for_update())
    if version is None:
        raise RuntimeError("回测项目缺少当前版本")
    run = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if run is None or run.application != "backtest":
        raise RuntimeError("当前回测版本关联的工作空间不存在")
    state = workflow_workspace_state(session, run)
    if state != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {state} 状态的回测工作流")
    executor = WorkflowExecutionService("backtest")
    version.parameters = payload
    version.updated_at = utc_now()
    if state == "DRAFT":
        create_workflow_attempt(session, run, payload, PROJECT_OUTPUTS)
        executor.submit_workspace(session, run, create_directory=True)
    else:
        run = executor.resubmit_workspace(session, run, payload, PROJECT_OUTPUTS)
    project.updated_at = utc_now()
    session.commit()
    return run


def create_backtest_version(session: Session, user_id: int, project_id: int, workflow_instance_id: int, remark: str) -> dict[str, Any]:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    existing = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.workflow_instance_id == workflow_instance_id, BacktestVersion.saved.is_(True)))
    if existing is not None:
        return serialize_version(session, existing)
    row = session.execute(
        select(BacktestVersion, WorkflowWorkspace, WorkflowAttempt, WorkflowInstance)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == BacktestVersion.workflow_workspace_id)
        .join(WorkflowAttempt, WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id)
        .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
        .where(
            BacktestVersion.project_id == project.id,
            BacktestVersion.is_current.is_(True),
            BacktestVersion.saved.is_(False),
            WorkflowWorkspace.user_id == user_id,
            WorkflowWorkspace.application == "backtest",
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowAttempt.is_current.is_(True),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError("当前未保存回测不存在或 workflow_instance_id 已失效")
    version, run, attempt, workflow = row
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流状态为 {workflow.state}，成功后才能保存版本")
    save_backtest_version(session, project, version, run, attempt, workflow, remark)
    create_backtest_draft(session, project, user_id, attempt.input_json)
    session.commit()
    return serialize_version(session, version)


def save_backtest_version(session: Session, project: BacktestProject, version: BacktestVersion, run: WorkflowWorkspace, attempt: WorkflowAttempt, workflow: WorkflowInstance, remark: str) -> BacktestVersion:
    if version.saved:
        return version
    parameters = attempt.input_json
    table = result_dataframe(session, run.user_id, workflow.workflow_instance_id, "backtest", "daily_portfolios", OUTPUT_FILES)
    if table.empty:
        raise ValueError("回测组合资产结果为空")
    metrics = backtest_metrics(parameters, table)
    summary = {
        "totalReturn": metrics["totalReturn"],
        "annualReturn": metrics["cagr"],
        "sharpeRatio": metrics["sharpe"],
        "annualVolatility": metrics["volatility"],
        "maxDrawdown": metrics["maxDrawdown"],
        "dailyWinningRate": metrics["winRate"],
    }
    version.workflow_instance_id = workflow.workflow_instance_id
    version.saved = True
    version.is_current = False
    version.remark = remark
    version.parameters = parameters
    version.summary = summary
    version.updated_at = utc_now()
    project.updated_at = utc_now()
    session.flush()
    return version


def list_backtest_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    rows = session.execute(
        select(BacktestVersion.id, BacktestVersion.version, BacktestVersion.saved, BacktestVersion.is_current, BacktestVersion.remark, BacktestVersion.workflow_instance_id, BacktestVersion.created_at)
        .where(BacktestVersion.project_id == project.id)
        .order_by(BacktestVersion.version.desc())
    ).mappings()
    return [dict(row) for row in rows]


def get_backtest_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    return serialize_version(session, version)


def update_backtest_version(session: Session, user_id: int, project_id: int, version_number: int, remark: str) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    version.remark = remark
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(session, version)


def delete_backtest_version(session: Session, user_id: int, project_id: int, version_number: int) -> int:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"回测版本不存在: {version_number}")
    if version.is_current:
        raise RuntimeError("当前未保存版本不能删除")
    source_workspace = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if source_workspace is None or source_workspace.application != "backtest":
        raise RuntimeError(f"回测版本 v{version_number} 关联的工作流不存在")

    research_ids = list(session.scalars(select(BacktestResearch.id).where(BacktestResearch.version_id == version.id)))
    research_workspace_ids = [] if not research_ids else list(session.scalars(select(BacktestResearch.workflow_workspace_id).where(BacktestResearch.id.in_(research_ids))))
    research_workspaces = [] if not research_workspace_ids else list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.id.in_(research_workspace_ids), WorkflowWorkspace.application == "sensitivity")))
    optimization_workspace_ids = list(session.scalars(select(BacktestOptimization.workflow_workspace_id).where(BacktestOptimization.version_id == version.id)))
    optimization_workspaces = [] if not optimization_workspace_ids else list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.id.in_(optimization_workspace_ids), WorkflowWorkspace.application == "optimization")))
    dependent_workspaces = [*research_workspaces, *optimization_workspaces]
    active_states = [state for workspace in dependent_workspaces if (state := workflow_workspace_state(session, workspace)) not in WORKSPACE_TERMINAL_STATES]
    if active_states:
        raise RuntimeError(f"版本仍有运行中的分析工作流: {sorted(set(active_states))}")

    artifacts = [resolve_workspace_artifacts(workspace) for workspace in [source_workspace, *dependent_workspaces]]
    if research_ids:
        session.execute(delete(BacktestResearch).where(BacktestResearch.id.in_(research_ids)))
    session.delete(version)
    session.flush()
    for workspace in [source_workspace, *dependent_workspaces]:
        session.delete(workspace)
    project.updated_at = utc_now()
    session.commit()
    for run_artifacts in artifacts:
        remove_workspace_artifacts(*run_artifacts)
    return version_number


def owned_project(session: Session, user_id: int, project_id: int) -> BacktestProject:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: BacktestProject) -> dict[str, Any]:
    latest_version = session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id, BacktestVersion.saved.is_(True)))
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == project.id, BacktestVersion.is_current.is_(True)))
    if version is None:
        raise RuntimeError("回测项目缺少当前版本")
    draft = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if draft is None or draft.application != "backtest":
        raise RuntimeError("当前回测版本关联的工作空间不存在")
    attempt = current_workflow_attempt(session, draft.id)
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id)) if attempt is not None else None
    return project_information(project, latest_version, version, draft, attempt, workflow)


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
        .where(BacktestVersion.project_id.in_(project_ids), BacktestVersion.saved.is_(True))
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
    version: BacktestVersion,
    draft: WorkflowWorkspace,
    attempt: WorkflowAttempt | None,
    workflow: WorkflowInstance | None,
) -> dict[str, Any]:
    draft_data = {
        "id": version.id,
        "version": version.version,
        "saved": version.saved,
        "workspace_id": draft.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow_attempt_state(attempt, workflow) if attempt is not None else "DRAFT",
        "error": (workflow.error if workflow is not None else None) or (attempt.error if attempt is not None else None),
        "parameters": version.parameters,
        "updated_at": max(attempt.updated_at, workflow.updated_at) if attempt is not None and workflow is not None else attempt.updated_at if attempt is not None else version.updated_at,
    }
    return {"id": project.id, "title": project.title, "latest_version": latest_version, "draft": draft_data, "created_at": project.created_at, "updated_at": project.updated_at}


def serialize_version(session: Session, version: BacktestVersion) -> dict[str, Any]:
    workflow_instance_id = version.workflow_instance_id
    if workflow_instance_id is None:
        attempt = current_workflow_attempt(session, version.workflow_workspace_id)
        workflow = None if attempt is None else session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id))
        workflow_instance_id = workflow.workflow_instance_id if workflow is not None else None
    return {"id": version.id, "project_id": version.project_id, "workflow_workspace_id": version.workflow_workspace_id, "workflow_instance_id": workflow_instance_id, "version": version.version, "saved": version.saved, "is_current": version.is_current, "remark": version.remark, "parameters": version.parameters, "summary": version.summary, "created_at": version.created_at, "updated_at": version.updated_at}


def create_backtest_draft(session: Session, project: BacktestProject, user_id: int, parameters: dict[str, Any]) -> BacktestVersion:
    version_number = (session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id)) or 0) + 1
    workspace = WorkflowWorkspace(user_id=user_id, application="backtest")
    session.add(workspace)
    session.flush()
    version = BacktestVersion(project_id=project.id, workflow_workspace_id=workspace.id, version=version_number, saved=False, is_current=True, parameters=parameters)
    session.add(version)
    session.flush()
    return version


def submit_backtest_batch(session: Session, user_id: int, project_id: int, items: Sequence[BatchRunItem[BacktestParameters]]) -> list[dict[str, Any]]:
    project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id, BacktestProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"回测项目不存在: {project_id}")
    workspace_ids, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(
        session,
        BacktestVersion,
        user_id,
        project.id,
        {item.client_id for item in items},
    )
    next_version = (session.scalar(select(func.max(BacktestVersion.version)).where(BacktestVersion.project_id == project.id)) or 0) + 1
    new_workspace_ids: list[int] = []
    for item in items:
        if item.client_id in workspace_ids:
            continue
        run = WorkflowWorkspace(user_id=user_id, application="backtest")
        session.add(run)
        session.flush()
        version = BacktestVersion(
            project_id=project.id,
            workflow_workspace_id=run.id,
            version=next_version,
            saved=False,
            is_current=False,
            remark=item.remark,
            parameters=item.parameters.model_dump(mode="json"),
        )
        session.add(version)
        next_version += 1
        attempt = create_workflow_attempt(
            session,
            run,
            item.parameters.model_dump(mode="json"),
            PROJECT_OUTPUTS,
            submission_state=BATCH_PENDING_STATE,
        )
        record_event(attempt, "AUTO_SAVE_VERSION", client_id=item.client_id, project_id=project.id, remark=item.remark)
        workspace_ids[item.client_id] = run.id
        new_workspace_ids.append(run.id)
    project.updated_at = utc_now()
    session.commit()
    submit_workspaces_now([*new_workspace_ids, *submission_retry_ids])
    finalize_auto_save_workspaces_now(auto_save_retry_ids)
    return [{"client_id": item.client_id, "workspace_id": workspace_ids[item.client_id]} for item in items]


def finalize_backtest_auto_save_workspace(session: Session, run: WorkflowWorkspace) -> None:
    attempt = require_current_workflow_attempt(session, run.id)
    metadata = auto_save_metadata(attempt)
    if metadata is None or attempt.submission_state != "AUTO_SAVE_PENDING":
        return
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id))
    if workflow is None or workflow.state != "SUCCESS":
        return
    try:
        version = session.scalar(select(BacktestVersion).where(BacktestVersion.workflow_workspace_id == run.id).with_for_update())
        if version is None:
            raise RuntimeError("批量回测工作空间缺少版本记录")
        project_id = metadata.get("project_id")
        if not isinstance(project_id, int) or project_id != version.project_id:
            raise ValueError("自动保存版本的项目标识无效")
        project = session.scalar(select(BacktestProject).where(BacktestProject.id == project_id).with_for_update())
        if project is None:
            raise FileNotFoundError(f"回测项目不存在: {project_id}")
        save_backtest_version(session, project, version, run, attempt, workflow, str(metadata.get("remark") or ""))
        attempt.submission_state = "AUTO_SAVE_COMPLETE"
        record_event(attempt, "AUTO_VERSION_SAVED", version=version.version)
        session.commit()
    except Exception as error:
        session.rollback()
        failed = current_workflow_attempt(session, run.id)
        if failed is not None:
            failed.error = str(error)
            failed.submission_state = "AUTO_SAVE_FAILED"
            record_event(failed, "AUTO_VERSION_SAVE_FAILED", error=str(error))
            session.commit()
        raise


def clean_summary_number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def backtest_metrics(parameters: dict[str, Any], portfolios: pd.DataFrame) -> dict[str, float | None]:
    missing = {"tradeDate", "ratio"} - set(portfolios)
    if missing:
        raise ValueError(f"回测组合资产结果缺少列: {', '.join(sorted(missing))}")
    ordered = portfolios.sort_values("tradeDate", kind="stable")
    returns = pd.to_numeric(ordered["ratio"], errors="coerce").fillna(0).to_numpy(dtype=float)
    returns = np.where(np.isfinite(returns), returns, 0)
    if not len(returns):
        raise ValueError("回测组合资产结果为空")
    annual_trading_days = int(parameters["annual_trading_days"])
    risk_free_rate = float(parameters["risk_free_rate"])
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        wealth = np.cumprod(1 + returns)
        growth = wealth[-1]
        annual_return = growth ** (annual_trading_days / len(returns)) - 1 if growth > 0 else np.nan
        annual_volatility = np.std(returns[1:], ddof=0) * np.sqrt(annual_trading_days) if len(returns) > 1 else np.nan
        sharpe_ratio = (annual_return - risk_free_rate) / annual_volatility if annual_volatility != 0 else np.nan
        maximum_drawdown = np.min(wealth / np.maximum.accumulate(np.concatenate(([1.0], wealth)))[1:] - 1)
        period_rate = (1 + risk_free_rate) ** (1 / annual_trading_days) - 1
        excess_returns = returns - period_rate
        downside = np.sqrt(np.square(excess_returns[excess_returns < 0]).sum() / len(excess_returns))
        sortino = excess_returns.mean() / downside * np.sqrt(annual_trading_days) if downside != 0 else np.nan
    nonzero = returns[returns != 0]
    maximum_drawdown = min(0.0, maximum_drawdown)
    total_fee = None
    if "totalFee" in ordered:
        cumulative_fees = pd.to_numeric(ordered["totalFee"], errors="coerce").to_numpy(dtype=float)
        previous_fees = np.concatenate(([0.0], cumulative_fees[:-1]))
        daily_fees = cumulative_fees - np.where(np.isfinite(previous_fees), previous_fees, 0)
        valid_fees = daily_fees[np.isfinite(daily_fees) & (daily_fees >= 0)]
        total_fee = clean_summary_number(valid_fees.sum()) if len(valid_fees) else None
    return {
        "totalReturn": clean_summary_number(growth - 1),
        "cagr": clean_summary_number(annual_return),
        "sharpe": clean_summary_number(sharpe_ratio),
        "sortino": clean_summary_number(sortino),
        "volatility": clean_summary_number(annual_volatility),
        "maxDrawdown": clean_summary_number(maximum_drawdown),
        "winRate": clean_summary_number(np.count_nonzero(returns > 0) / len(nonzero) if len(nonzero) else 0),
        "calmar": clean_summary_number(annual_return / abs(maximum_drawdown)) if maximum_drawdown != 0 else None,
        "totalFee": total_fee,
    }


def create_backtest_optimization(
    session: Session,
    user: User,
    project_id: int,
    version_number: int,
    settings: OptimizationSettings,
) -> dict[str, Any]:
    """基于一个已保存版本提交一次滚动参数调优工作流。"""
    version = owned_batch_version(session, user, project_id, version_number)
    parameters = OptimizationParameters.model_validate({
        **version.parameters,
        **settings.model_dump(mode="json"),
    })
    workspace = WorkflowWorkspace(user_id=user.id, application="optimization")
    session.add(workspace)
    session.flush()
    optimization = BacktestOptimization(
        version_id=version.id,
        workflow_workspace_id=workspace.id,
    )
    session.add(optimization)
    session.flush()
    create_workflow_attempt(
        session,
        workspace,
        parameters.model_dump(mode="json"),
        [algorithm.value for algorithm in parameters.algorithms],
    )
    session.commit()
    WorkflowExecutionService("optimization").submit_workspace(
        session,
        workspace,
        create_directory=True,
    )
    session.expire_all()
    return get_backtest_optimization(session, user, optimization.id)


def list_backtest_optimizations(
    session: Session,
    user: User,
    project_id: int,
    version_number: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """分页读取一个回测版本的全部参数调优报告。"""
    version = owned_batch_version(session, user, project_id, version_number)
    total = int(session.scalar(select(func.count(BacktestOptimization.id)).where(BacktestOptimization.version_id == version.id)) or 0)
    rows = session.execute(
        optimization_statement()
        .where(BacktestOptimization.version_id == version.id)
        .order_by(BacktestOptimization.created_at.desc(), BacktestOptimization.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [serialize_backtest_optimization(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_backtest_optimization(
    session: Session,
    user: User,
    optimization_id: int,
) -> dict[str, Any]:
    """读取当前用户的一条参数调优报告。"""
    row = session.execute(
        optimization_statement().where(
            BacktestOptimization.id == optimization_id,
            BacktestProject.user_id == user.id,
        )
    ).one_or_none()
    if row is None:
        raise FileNotFoundError(f"参数调优报告不存在: {optimization_id}")
    return serialize_backtest_optimization(*row)


def delete_backtest_optimization(
    session: Session,
    user: User,
    optimization_id: int,
) -> int:
    """删除当前用户的一条参数调优报告及其独占工作空间。"""
    row = session.execute(
        select(BacktestOptimization, WorkflowWorkspace)
        .join(BacktestVersion, BacktestVersion.id == BacktestOptimization.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .join(
            WorkflowWorkspace,
            WorkflowWorkspace.id == BacktestOptimization.workflow_workspace_id,
        )
        .where(
            BacktestOptimization.id == optimization_id,
            BacktestProject.user_id == user.id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError(f"参数调优报告不存在: {optimization_id}")
    optimization, workspace = row
    delete_analysis_workspace(
        session,
        optimization,
        workspace,
        expected_application="optimization",
        label="参数调优报告",
    )
    return optimization_id


def optimization_statement() -> Any:
    return (
        select(
            BacktestOptimization,
            BacktestVersion.project_id,
            BacktestVersion.version,
            WorkflowWorkspace,
            WorkflowAttempt,
            WorkflowInstance,
        )
        .join(BacktestVersion, BacktestVersion.id == BacktestOptimization.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == BacktestOptimization.workflow_workspace_id)
        .outerjoin(
            WorkflowAttempt,
            and_(
                WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id,
                WorkflowAttempt.is_current.is_(True),
            ),
        )
        .outerjoin(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
    )


def serialize_backtest_optimization(
    optimization: BacktestOptimization,
    project_id: int,
    version: int,
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt | None,
    workflow: WorkflowInstance | None,
) -> dict[str, Any]:
    if attempt is None:
        raise RuntimeError(f"参数调优报告 {optimization.id} 缺少工作流提交尝试")
    updated_at = max(
        attempt.updated_at,
        workflow.updated_at if workflow is not None else attempt.updated_at,
    )
    return {
        "id": optimization.id,
        "project_id": project_id,
        "version": version,
        "workflow_workspace_id": workspace.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": workflow_attempt_state(attempt, workflow),
        "error": (workflow.error if workflow is not None else None) or attempt.error,
        "parameters": attempt.input_json,
        "created_at": optimization.created_at,
        "updated_at": updated_at,
    }


def optimization_workflow_instance_id(
    session: Session,
    user_id: int,
    optimization_id: int,
) -> int:
    workflow_instance_id = session.scalar(
        select(WorkflowInstance.workflow_instance_id)
        .join(WorkflowAttempt, WorkflowAttempt.id == WorkflowInstance.workflow_attempt_id)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == WorkflowAttempt.workflow_workspace_id)
        .join(BacktestOptimization, BacktestOptimization.workflow_workspace_id == WorkflowWorkspace.id)
        .join(BacktestVersion, BacktestVersion.id == BacktestOptimization.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(
            BacktestOptimization.id == optimization_id,
            BacktestProject.user_id == user_id,
            WorkflowAttempt.is_current.is_(True),
        )
    )
    if workflow_instance_id is None:
        raise FileNotFoundError(f"参数调优报告尚未关联工作流实例: {optimization_id}")
    return int(workflow_instance_id)


def create_batch_research(session: Session, user: User, request: BatchResearchCreate) -> dict[str, Any]:
    version = owned_batch_version(session, user, request.project_id, request.version)
    base = BacktestParameters.model_validate(version.parameters)
    cases = []
    for number, item in enumerate(request.items, start=1):
        candidate = BacktestParameters.model_validate(item.parameters)
        validate_research_parameter_set(base, candidate, request.analysis_type, number)
        cases.append({
            "params": candidate.params,
            "commission": float(candidate.config["commission"]),
        })
    parameters = SensitivityParameters.model_validate({
        **base.model_dump(mode="json"),
        "analysis_type": request.analysis_type,
        "cases": cases,
    })
    workspace = WorkflowWorkspace(user_id=user.id, application="sensitivity")
    session.add(workspace)
    session.flush()
    research = BacktestResearch(
        version_id=version.id,
        workflow_workspace_id=workspace.id,
        analysis_type=request.analysis_type,
        description=request.description,
    )
    session.add(research)
    session.flush()
    attempt = create_workflow_attempt(
        session,
        workspace,
        parameters.model_dump(mode="json"),
        BATCH_OUTPUTS,
    )
    record_event(attempt, "BACKTEST_RESEARCH", research_id=research.id)
    session.commit()
    WorkflowExecutionService("sensitivity").submit_workspace(
        session,
        workspace,
        create_directory=True,
    )
    session.expire_all()
    return get_batch_research(session, user, research.id)


def create_fee_analysis(session: Session, user: User, project_id: int, version: int, request: FeeAnalysisCreate) -> dict[str, Any]:
    source = owned_batch_version(session, user, project_id, version)
    base = BacktestParameters.model_validate(source.parameters).model_dump(mode="json")
    return create_batch_research(
        session,
        user,
        BatchResearchCreate(
            analysis_type=BatchAnalysisType.FEE_ANALYSIS,
            project_id=project_id,
            version=version,
            items=[{"parameters": with_commission(base, rate)} for rate in request.rates],
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
    statement = batch_research_statement().where(BacktestProject.user_id == user.id)
    if project_id is not None:
        statement = statement.where(BacktestVersion.project_id == project_id)
    if version is not None:
        statement = statement.where(BacktestVersion.version == version)
    if analysis_type is not None:
        statement = statement.where(BacktestResearch.analysis_type == analysis_type)
    total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = session.execute(
        statement.order_by(BacktestResearch.created_at.desc(), BacktestResearch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [serialize_batch_research(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_batch_research(session: Session, user: User, research_id: int) -> dict[str, Any]:
    statement = batch_research_statement().where(
        BacktestResearch.id == research_id,
        BacktestProject.user_id == user.id,
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        raise FileNotFoundError(f"批量研究不存在: {research_id}")
    research, _, _, workspace, _, workflow = row
    if (
        workflow is not None
        and workflow.state == "SUCCESS"
        and (
            research.result_workflow_instance_id != workflow.workflow_instance_id
            or research.completed_count is None
            or research.failed_count is None
        )
    ):
        finalize_sensitivity_workspace(session, workspace)
        row = session.execute(statement).one()
    return serialize_batch_research(*row)


def delete_batch_research(session: Session, user: User, research_id: int) -> int:
    """删除当前用户的一条手续费或参数敏感性分析及其独占工作空间。"""
    row = session.execute(
        select(BacktestResearch, WorkflowWorkspace)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .join(
            WorkflowWorkspace,
            WorkflowWorkspace.id == BacktestResearch.workflow_workspace_id,
        )
        .where(
            BacktestResearch.id == research_id,
            BacktestProject.user_id == user.id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError(f"批量研究不存在: {research_id}")
    research, workspace = row
    delete_analysis_workspace(
        session,
        research,
        workspace,
        expected_application="sensitivity",
        label=BATCH_ANALYSIS_LABELS.get(research.analysis_type, "批量研究"),
    )
    return research_id


def delete_analysis_workspace(
    session: Session,
    record: BacktestOptimization | BacktestResearch,
    workspace: WorkflowWorkspace,
    *,
    expected_application: str,
    label: str,
) -> None:
    if workspace.application != expected_application:
        raise RuntimeError(f"{label}关联的工作空间类型错误: {workspace.application}")
    state = workflow_workspace_state(session, workspace)
    if state != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"{state} 状态的{label}不能删除")
    artifacts = resolve_workspace_artifacts(workspace)
    session.delete(record)
    session.flush()
    session.delete(workspace)
    session.commit()
    try:
        remove_workspace_artifacts(*artifacts)
    except OSError:
        LOGGER.exception(
            "数据库记录已删除，但清理%s工作空间产物失败: %s/%s",
            label,
            artifacts[1],
            artifacts[2],
        )


def owned_batch_version(session: Session, user: User, project_id: int, version: int) -> BacktestVersion:
    row = session.scalar(
        select(BacktestVersion)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(BacktestVersion.project_id == project_id, BacktestVersion.version == version, BacktestVersion.saved.is_(True), BacktestProject.user_id == user.id)
    )
    if row is None:
        raise FileNotFoundError(f"策略回测版本不存在: {project_id}/v{version}")
    return row


def with_commission(parameters: dict[str, Any], rate: float) -> dict[str, Any]:
    result = deepcopy(parameters)
    config = dict(result["config"])
    config["commission"] = rate
    result["config"] = config
    return result


def serialize_batch_research(
    research: BacktestResearch,
    project_id: int,
    version: int,
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt | None,
    workflow: WorkflowInstance | None,
) -> dict[str, Any]:
    if attempt is None:
        raise RuntimeError(f"批量研究 {research.id} 缺少工作流提交尝试")
    state = workflow_attempt_state(attempt, workflow)
    requested_count = len(attempt.input_json.get("cases", []))
    result_is_current = (
        workflow is not None
        and research.result_workflow_instance_id == workflow.workflow_instance_id
        and research.completed_count is not None
        and research.failed_count is not None
    )
    current_result_error = (
        research.result_error
        if workflow is not None
        and workflow.state == "SUCCESS"
        and research.result_workflow_instance_id == workflow.workflow_instance_id
        and not result_is_current
        else None
    )
    if state in BATCH_SUCCESS_STATES and result_is_current:
        completed = int(research.completed_count or 0)
        failed = int(research.failed_count or 0)
    elif state in BATCH_SUCCESS_STATES:
        completed = 0
        failed = 0
        state = "RESULT_FAILED" if current_result_error else "RESULT_PENDING"
    else:
        completed = 0
        failed = requested_count if state in WORKSPACE_FAILURE_STATES else 0
    updated_at = max(
        attempt.updated_at,
        workflow.updated_at if workflow is not None else attempt.updated_at,
    )
    return {
        "id": research.id,
        "analysis_type": research.analysis_type,
        "analysis_type_label": BATCH_ANALYSIS_LABELS.get(research.analysis_type, research.analysis_type),
        "project_id": project_id,
        "version": version,
        "description": research.description,
        "workflow_workspace_id": workspace.id,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "state": state,
        "requested_count": requested_count,
        "completed_count": completed,
        "failed_count": failed,
        "error": current_result_error or (workflow.error if workflow is not None else None) or attempt.error,
        "parameters": attempt.input_json,
        "created_at": research.created_at,
        "updated_at": updated_at,
    }


def finalize_sensitivity_workspace(
    session: Session,
    workspace: WorkflowWorkspace,
) -> None:
    """校验当前敏感性结果文件，并把真实组合计数绑定到当前实例。"""
    if workspace.application != "sensitivity":
        return
    research = session.scalar(
        select(BacktestResearch)
        .where(BacktestResearch.workflow_workspace_id == workspace.id)
        .with_for_update()
    )
    if research is None:
        raise RuntimeError(f"敏感性工作空间 {workspace.id} 缺少研究记录")
    attempt = require_current_workflow_attempt(session, workspace.id)
    workflow = session.scalar(
        select(WorkflowInstance).where(
            WorkflowInstance.workflow_attempt_id == attempt.id
        )
    )
    if workflow is None or workflow.state != "SUCCESS":
        return
    if (
        research.result_workflow_instance_id == workflow.workflow_instance_id
        and research.completed_count is not None
        and research.failed_count is not None
    ):
        return

    try:
        requested_cases = attempt.input_json.get("cases")
        if not isinstance(requested_cases, list) or not requested_cases:
            raise ValueError("敏感性分析请求缺少 cases")
        table = result_dataframe(
            session,
            workspace.user_id,
            workflow.workflow_instance_id,
            "sensitivity",
            "results",
            SENSITIVITY_OUTPUT_FILES,
        )
        missing = {"case_index", "status"} - set(table)
        if missing:
            raise ValueError(
                f"敏感性分析结果缺少列: {', '.join(sorted(missing))}"
            )
        if len(table) != len(requested_cases):
            raise ValueError(
                "敏感性分析结果行数与请求组合数不一致: "
                f"{len(table)} != {len(requested_cases)}"
            )
        indices = pd.to_numeric(table["case_index"], errors="coerce")
        if indices.isna().any() or (indices % 1 != 0).any():
            raise ValueError("敏感性分析结果包含无效 case_index")
        actual_indices = [int(value) for value in indices]
        expected_indices = list(range(1, len(requested_cases) + 1))
        if sorted(actual_indices) != expected_indices:
            raise ValueError("敏感性分析结果 case_index 不完整或重复")
        statuses = table["status"].astype("string")
        invalid_statuses = sorted(
            set(statuses.dropna().astype(str)) - {"SUCCESS", "FAILURE"}
        )
        if statuses.isna().any() or invalid_statuses:
            raise ValueError(
                f"敏感性分析结果包含无效状态: {invalid_statuses}"
            )
        completed_count = int((statuses == "SUCCESS").sum())
        failed_count = int((statuses == "FAILURE").sum())
        if completed_count + failed_count != len(requested_cases):
            raise ValueError("敏感性分析结果状态数量与请求组合数不一致")
    except Exception as error:
        previous_error = research.result_error
        research.result_workflow_instance_id = workflow.workflow_instance_id
        research.completed_count = None
        research.failed_count = None
        research.result_error = str(error)
        if previous_error != str(error):
            record_event(
                attempt,
                "SENSITIVITY_RESULT_FAILED",
                workflow_instance_id=workflow.workflow_instance_id,
                error=str(error),
            )
        session.commit()
        return

    research.result_workflow_instance_id = workflow.workflow_instance_id
    research.completed_count = completed_count
    research.failed_count = failed_count
    research.result_error = None
    record_event(
        attempt,
        "SENSITIVITY_RESULT_COLLECTED",
        workflow_instance_id=workflow.workflow_instance_id,
        completed_count=completed_count,
        failed_count=failed_count,
    )
    session.commit()


def batch_research_statement() -> Any:
    return (
        select(
            BacktestResearch,
            BacktestVersion.project_id,
            BacktestVersion.version,
            WorkflowWorkspace,
            WorkflowAttempt,
            WorkflowInstance,
        )
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == BacktestResearch.workflow_workspace_id)
        .outerjoin(
            WorkflowAttempt,
            and_(
                WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id,
                WorkflowAttempt.is_current.is_(True),
            ),
        )
        .outerjoin(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
    )


def validate_research_parameter_set(
    base: BacktestParameters,
    candidate: BacktestParameters,
    analysis_type: BatchAnalysisType,
    number: int,
) -> None:
    """限制批量研究只能改变目标手续费或策略参数。"""
    base_data = base.model_dump(mode="json")
    candidate_data = candidate.model_dump(mode="json")
    if analysis_type == BatchAnalysisType.FEE_ANALYSIS:
        if candidate.params != base.params:
            raise ValueError(f"items[{number}] 手续费分析不能修改 params")
        base_config = dict(base_data.pop("config"))
        candidate_config = dict(candidate_data.pop("config"))
        base_config.pop("commission", None)
        candidate_config.pop("commission", None)
        if base_data != candidate_data or base_config != candidate_config:
            raise ValueError(f"items[{number}] 手续费分析只能修改 config.commission")
    else:
        base_data.pop("params")
        candidate_data.pop("params")
        if base_data != candidate_data:
            raise ValueError(f"items[{number}] 参数敏感性分析只能修改 params")


def sensitivity_workflow_instance_id(
    session: Session,
    user_id: int,
    research_id: int,
) -> int:
    workflow_instance_id = session.scalar(
        select(WorkflowInstance.workflow_instance_id)
        .join(WorkflowAttempt, WorkflowAttempt.id == WorkflowInstance.workflow_attempt_id)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == WorkflowAttempt.workflow_workspace_id)
        .join(BacktestResearch, BacktestResearch.workflow_workspace_id == WorkflowWorkspace.id)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(
            BacktestResearch.id == research_id,
            BacktestProject.user_id == user_id,
            WorkflowAttempt.is_current.is_(True),
        )
    )
    if workflow_instance_id is None:
        raise FileNotFoundError(f"批量研究尚未关联工作流实例: {research_id}")
    return int(workflow_instance_id)
