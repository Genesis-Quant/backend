"""Backtest workflow submission, strategy projects, versions, and results."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from math import isfinite
from typing import Any

from fastapi import Response
import numpy as np
import pandas as pd
from runtime import BacktestParameters
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.orm import Session

from core.apps.backtest.models import (
    BacktestResearch,
    BacktestResearchItem,
    BacktestProject,
    BacktestVersion,
)
from core.apps.schemas import BatchRunItem
from core.apps.backtest.schemas import (
    BatchAnalysisType,
    BatchResearchCreate,
    BatchResearchItemCreate,
    FeeAnalysisCreate,
)
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    BATCH_PENDING_STATE,
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
    submit_workspaces_now,
    require_current_workflow_attempt,
    workflow_attempt_state,
    workflow_workspace_state,
)
from core.utils.results import read_result_dataframe, result_dataframe, result_files, result_response
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
        select(BacktestResearchItem.workflow_workspace_id)
        .join(BacktestResearch, BacktestResearch.id == BacktestResearchItem.research_id)
        .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
        .where(BacktestVersion.project_id == project.id)
    )
    runs = list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.id.in_(version_workspace_ids.union_all(research_workspace_ids)))))
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
    research_workspace_ids = [] if not research_ids else list(session.scalars(select(BacktestResearchItem.workflow_workspace_id).where(BacktestResearchItem.research_id.in_(research_ids))))
    research_workspaces = [] if not research_workspace_ids else list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.id.in_(research_workspace_ids), WorkflowWorkspace.application == "backtest")))
    active_states = [state for workspace in research_workspaces if (state := workflow_workspace_state(session, workspace)) not in WORKSPACE_TERMINAL_STATES]
    if active_states:
        raise RuntimeError(f"版本仍有运行中的批量分析工作流: {sorted(set(active_states))}")

    artifacts = [resolve_workspace_artifacts(workspace) for workspace in [source_workspace, *research_workspaces]]
    if research_ids:
        session.execute(delete(BacktestResearchItem).where(BacktestResearchItem.research_id.in_(research_ids)))
        session.execute(delete(BacktestResearch).where(BacktestResearch.id.in_(research_ids)))
    session.delete(version)
    session.flush()
    for workspace in [source_workspace, *research_workspaces]:
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


def create_batch_research(session: Session, user: User, request: BatchResearchCreate) -> dict[str, Any]:
    outputs = batch_outputs(request.analysis_type)
    version = owned_batch_version(session, user, request.project_id, request.version)
    base_parameters = BacktestParameters.model_validate(version.parameters).model_dump(mode="json")
    parameters = [BacktestParameters.model_validate(item.parameters).model_dump(mode="json") for item in request.items]
    research = BacktestResearch(version_id=version.id, analysis_type=request.analysis_type, description=request.description)
    session.add(research)
    session.flush()
    workspace_ids: list[int] = []
    for item_parameters in parameters:
        workspace = WorkflowWorkspace(user_id=user.id, application="backtest")
        session.add(workspace)
        session.flush()
        attempt = create_workflow_attempt(
            session,
            workspace,
            item_parameters,
            outputs,
            submission_state=BATCH_PENDING_STATE,
        )
        record_event(attempt, "BACKTEST_RESEARCH_ITEM", research_id=research.id)
        workspace_ids.append(workspace.id)
        session.add(
            BacktestResearchItem(
                research_id=research.id,
                workflow_workspace_id=workspace.id,
                parameter_overrides=parameter_overrides(base_parameters, item_parameters),
            )
        )
    session.commit()
    submit_workspaces_now(workspace_ids)
    session.expire_all()
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
    return serialize_batch_research(research, project_id, version, executions, include_items=True)


def calculate_batch_research_results(session: Session, user: User, research_id: int) -> dict[str, Any]:
    research = get_batch_research(session, user, research_id)
    rows = session.execute(
        select(BacktestResearchItem, WorkflowWorkspace, WorkflowAttempt, WorkflowInstance)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == BacktestResearchItem.workflow_workspace_id)
        .join(WorkflowAttempt, and_(WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id, WorkflowAttempt.is_current.is_(True)))
        .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
        .where(BacktestResearchItem.research_id == research_id)
        .order_by(BacktestResearchItem.id)
    ).all()
    pending: list[tuple[int, str, str, int, list[str], dict[str, Any]]] = []
    for research_item, workspace, attempt, workflow in rows:
        if workflow.state not in BATCH_SUCCESS_STATES:
            continue
        if research_item.result_workflow_instance_id == workflow.workflow_instance_id and research_item.metrics is not None:
            continue
        pending.append((research_item.id, workspace.application, workspace.workspace_key, workflow.workflow_instance_id, list(attempt.requested_outputs), attempt.input_json))
    if pending:
        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
            calculated = list(executor.map(calculate_batch_research_item, pending))
        for research_item_id, workflow_instance_id, metrics, result_error in calculated:
            current_workspace_ids = (
                select(WorkflowAttempt.workflow_workspace_id)
                .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
                .where(
                    WorkflowAttempt.is_current.is_(True),
                    WorkflowInstance.workflow_instance_id == workflow_instance_id,
                    WorkflowInstance.state.in_(BATCH_SUCCESS_STATES),
                )
            )
            session.execute(
                update(BacktestResearchItem)
                .where(
                    BacktestResearchItem.id == research_item_id,
                    BacktestResearchItem.research_id == research_id,
                    BacktestResearchItem.workflow_workspace_id.in_(current_workspace_ids),
                )
                .values(
                    result_workflow_instance_id=workflow_instance_id,
                    metrics=metrics,
                    result_error=result_error,
                )
                .execution_options(synchronize_session=False)
            )
        session.commit()
        session.expire_all()
        return get_batch_research(session, user, research_id)
    return research


def calculate_batch_research_item(item: tuple[int, str, str, int, list[str], dict[str, Any]]) -> tuple[int, int, dict[str, float | None] | None, str | None]:
    research_item_id, application, workspace_key, workflow_instance_id, outputs, parameters = item
    try:
        if "daily_portfolios" not in outputs:
            raise FileNotFoundError("批量回测未请求结果: daily_portfolios")
        portfolios = read_result_dataframe(application, workspace_key, workflow_instance_id, "daily_portfolios", OUTPUT_FILES)
        return research_item_id, workflow_instance_id, backtest_metrics(parameters, portfolios), None
    except Exception as error:
        return research_item_id, workflow_instance_id, None, str(error)


def owned_batch_version(session: Session, user: User, project_id: int, version: int) -> BacktestVersion:
    row = session.scalar(
        select(BacktestVersion)
        .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
        .where(BacktestVersion.project_id == project_id, BacktestVersion.version == version, BacktestVersion.saved.is_(True), BacktestProject.user_id == user.id)
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
        select(BacktestResearchItem, WorkflowWorkspace, WorkflowAttempt, WorkflowInstance)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == BacktestResearchItem.workflow_workspace_id)
        .join(WorkflowAttempt, and_(WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id, WorkflowAttempt.is_current.is_(True)))
        .outerjoin(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
        .where(BacktestResearchItem.research_id.in_(research_ids))
        .order_by(BacktestResearchItem.research_id, BacktestResearchItem.id)
    ).all()
    for research_item, workspace, attempt, workflow in rows:
        workflow_instance_id = workflow.workflow_instance_id if workflow is not None else None
        result_is_current = workflow_instance_id is not None and research_item.result_workflow_instance_id == workflow_instance_id
        result[research_item.research_id].append(
            {
                "id": research_item.id,
                "workflow_workspace_id": workspace.id,
                "workflow_instance_id": workflow_instance_id,
                "state": workflow_attempt_state(attempt, workflow),
                "parameters": attempt.input_json,
                "error": (workflow.error if workflow is not None else None) or attempt.error,
                "metrics": research_item.metrics if result_is_current else None,
                "result_error": research_item.result_error if result_is_current else None,
            }
        )
    return result


def serialize_batch_research(
    research: BacktestResearch,
    project_id: int,
    version: int,
    items: list[dict[str, Any]],
    *,
    include_items: bool = False,
) -> dict[str, Any]:
    completed = sum(item["state"] in BATCH_SUCCESS_STATES and item.get("metrics") is not None and not item.get("result_error") for item in items)
    failed = sum(item["state"] in WORKSPACE_FAILURE_STATES or bool(item.get("result_error")) for item in items)
    workflow_pending = any(item["state"] not in WORKSPACE_TERMINAL_STATES for item in items)
    result_pending = any(item["state"] in BATCH_SUCCESS_STATES and item.get("metrics") is None and not item.get("result_error") for item in items)
    if not items or workflow_pending:
        state = "RUNNING"
    elif result_pending:
        state = "RESULT_PENDING"
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
        "requested_count": len(items),
        "completed_count": completed,
        "failed_count": failed,
        "created_at": research.created_at,
    }
    if include_items:
        errors = [message for item in items for message in (item.get("error"), item.get("result_error")) if message]
        result["error"] = "; ".join(dict.fromkeys(errors)) or None
        result["items"] = items
    return result
