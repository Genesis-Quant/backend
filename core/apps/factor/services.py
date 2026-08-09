"""Factor workflow submission, research projects, versions, and results."""

from collections.abc import Sequence
from math import isfinite, sqrt
from typing import Any

from fastapi import Response
import numpy as np
import pandas as pd
from runtime.apps.factor.schema import FactorAnalysisParameters
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from core.apps.factor.models import FactorProject, FactorVersion
from core.apps.schemas import BatchRunItem
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    BATCH_PENDING_STATE,
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
from core.utils.results import result_dataframe, result_files, result_response
from core.utils.time import utc_now

OUTPUT_FILES = {
    "processed_data": "factor_processed.parquet",
    "information_coefficient": "factor_information_coefficients.parquet",
    "group_returns": "factor_group_returns.parquet",
}
PROJECT_OUTPUTS = ["information_coefficient", "group_returns"]


def factor_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, workflow_instance_id, "factor", OUTPUT_FILES)


def factor_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(session, user_id, workflow_instance_id, name, "factor", OUTPUT_FILES)


def list_factor_projects(session: Session, user_id: int, page: int, page_size: int) -> dict[str, Any]:
    statement = select(FactorProject).where(FactorProject.user_id == user_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    projects = session.scalars(statement.order_by(FactorProject.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": project_summaries(session, projects), "page": page, "page_size": page_size, "total": total}


def create_factor_project(session: Session, user_id: int, title: str) -> dict[str, Any]:
    project = FactorProject(user_id=user_id, title=title)
    session.add(project)
    session.flush()
    create_factor_draft(session, project, user_id, {})
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
    runs = list(session.scalars(
        select(WorkflowWorkspace)
        .join(FactorVersion, FactorVersion.workflow_workspace_id == WorkflowWorkspace.id)
        .where(FactorVersion.project_id == project.id)
    ))
    running = [state for run in runs if (state := workflow_workspace_state(session, run)) != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES]
    if running:
        raise RuntimeError(f"项目仍有运行中的因子工作流: {sorted(set(running))}")
    artifacts = [
        resolve_workspace_artifacts(run)
        for run in runs
    ]
    session.execute(delete(FactorVersion).where(FactorVersion.project_id == project.id))
    for run in runs:
        session.delete(run)
    session.delete(project)
    session.commit()
    for run_artifacts in artifacts:
        remove_workspace_artifacts(*run_artifacts)
    return project_id


def submit_project_analysis(session: Session, user_id: int, project_id: int, payload: dict[str, Any]) -> WorkflowWorkspace:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.is_current.is_(True)).with_for_update())
    if version is None:
        raise RuntimeError("因子项目缺少当前版本")
    run = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if run is None or run.application != "factor":
        raise RuntimeError("当前因子版本关联的工作空间不存在")
    state = workflow_workspace_state(session, run)
    if state != "DRAFT" and state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"项目已有 {state} 状态的因子工作流")
    executor = WorkflowExecutionService("factor")
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


def create_factor_version(session: Session, user_id: int, project_id: int, workflow_instance_id: int, remark: str) -> dict[str, Any]:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    existing = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.workflow_instance_id == workflow_instance_id, FactorVersion.saved.is_(True)))
    if existing is not None:
        return serialize_version(session, existing)
    row = session.execute(
        select(FactorVersion, WorkflowWorkspace, WorkflowAttempt, WorkflowInstance)
        .join(WorkflowWorkspace, WorkflowWorkspace.id == FactorVersion.workflow_workspace_id)
        .join(WorkflowAttempt, WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id)
        .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
        .where(
            FactorVersion.project_id == project.id,
            FactorVersion.is_current.is_(True),
            FactorVersion.saved.is_(False),
            WorkflowWorkspace.user_id == user_id,
            WorkflowWorkspace.application == "factor",
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowAttempt.is_current.is_(True),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        raise FileNotFoundError("当前未保存分析不存在或 workflow_instance_id 已失效")
    version, run, attempt, workflow = row
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流状态为 {workflow.state}，成功后才能保存版本")
    save_factor_version(session, project, version, run, attempt, workflow, remark)
    create_factor_draft(session, project, user_id, attempt.input_json)
    session.commit()
    return serialize_version(session, version)


def save_factor_version(session: Session, project: FactorProject, version: FactorVersion, run: WorkflowWorkspace, attempt: WorkflowAttempt, workflow: WorkflowInstance, remark: str) -> FactorVersion:
    if version.saved:
        return version
    parameters = attempt.input_json
    information = result_dataframe(session, run.user_id, workflow.workflow_instance_id, "factor", "information_coefficient", OUTPUT_FILES)
    groups = result_dataframe(session, run.user_id, workflow.workflow_instance_id, "factor", "group_returns", OUTPUT_FILES)
    metrics = factor_metrics(parameters, information, groups)
    validate_metric_dimensions(parameters, metrics)
    version.workflow_instance_id = workflow.workflow_instance_id
    version.saved = True
    version.is_current = False
    version.remark = remark
    version.parameters = parameters
    version.metrics = metrics
    version.updated_at = utc_now()
    project.updated_at = utc_now()
    session.flush()
    return version


def list_factor_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    rows = session.execute(
        select(FactorVersion.id, FactorVersion.version, FactorVersion.saved, FactorVersion.is_current, FactorVersion.remark, FactorVersion.workflow_instance_id, FactorVersion.created_at)
        .where(FactorVersion.project_id == project.id)
        .order_by(FactorVersion.version.desc())
    ).mappings()
    return [dict(row) for row in rows]


def get_factor_version(session: Session, user_id: int, project_id: int, version_number: int) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"因子版本不存在: {version_number}")
    return serialize_version(session, version)


def update_factor_version(session: Session, user_id: int, project_id: int, version_number: int, remark: str) -> dict[str, Any]:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"因子版本不存在: {version_number}")
    version.remark = remark
    project.updated_at = utc_now()
    session.commit()
    return serialize_version(session, version)


def delete_factor_version(session: Session, user_id: int, project_id: int, version_number: int) -> int:
    project = owned_project(session, user_id, project_id)
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.version == version_number))
    if version is None:
        raise FileNotFoundError(f"因子版本不存在: {version_number}")
    if version.is_current:
        raise RuntimeError("当前未保存版本不能删除")
    run = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if run is None or run.application != "factor":
        raise RuntimeError(f"因子版本 v{version_number} 关联的工作流不存在")
    state = workflow_workspace_state(session, run)
    if state not in WORKSPACE_TERMINAL_STATES:
        raise RuntimeError(f"{state} 状态的因子版本不能删除")
    artifacts = resolve_workspace_artifacts(run)
    session.delete(version)
    session.flush()
    session.delete(run)
    project.updated_at = utc_now()
    session.commit()
    remove_workspace_artifacts(*artifacts)
    return version_number


def owned_project(session: Session, user_id: int, project_id: int) -> FactorProject:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id))
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    return project


def serialize_project(session: Session, project: FactorProject) -> dict[str, Any]:
    latest_version = session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id, FactorVersion.saved.is_(True)))
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id, FactorVersion.is_current.is_(True)))
    if version is None:
        raise RuntimeError("因子项目缺少当前版本")
    draft = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    if draft is None or draft.application != "factor":
        raise RuntimeError("当前因子版本关联的工作空间不存在")
    attempt = current_workflow_attempt(session, draft.id)
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id)) if attempt is not None else None
    return project_information(project, latest_version, version, draft, attempt, workflow)


def project_summaries(
    session: Session,
    projects: Sequence[FactorProject],
) -> list[dict[str, Any]]:
    project_ids = [project.id for project in projects]
    if not project_ids:
        return []
    latest_numbers = (
        select(
            FactorVersion.project_id,
            func.max(FactorVersion.version).label("version"),
        )
        .where(FactorVersion.project_id.in_(project_ids), FactorVersion.saved.is_(True))
        .group_by(FactorVersion.project_id)
        .subquery()
    )
    latest_versions = session.execute(
        select(FactorVersion.project_id, FactorVersion.version, FactorVersion.metrics).join(
            latest_numbers,
            and_(FactorVersion.project_id == latest_numbers.c.project_id, FactorVersion.version == latest_numbers.c.version),
        )
    ).all()
    latest_by_project = {project_id: (version, first_metric(metrics)) for project_id, version, metrics in latest_versions}
    return [
        {
            "id": project.id,
            "title": project.title,
            "latest_version": latest_by_project[project.id][0] if project.id in latest_by_project else None,
            "latest_metric": latest_by_project[project.id][1] if project.id in latest_by_project else None,
            "updated_at": project.updated_at,
        }
        for project in projects
    ]


def first_metric(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    if metrics is None:
        return None
    for returns in metrics.values():
        if isinstance(returns, dict):
            for metric in returns.values():
                if isinstance(metric, dict):
                    return metric
    return None


def project_information(
    project: FactorProject,
    latest_version: int | None,
    version: FactorVersion,
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


def serialize_version(session: Session, version: FactorVersion) -> dict[str, Any]:
    workflow_instance_id = version.workflow_instance_id
    if workflow_instance_id is None:
        attempt = current_workflow_attempt(session, version.workflow_workspace_id)
        workflow = None if attempt is None else session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id))
        workflow_instance_id = workflow.workflow_instance_id if workflow is not None else None
    return {"id": version.id, "project_id": version.project_id, "workflow_workspace_id": version.workflow_workspace_id, "workflow_instance_id": workflow_instance_id, "version": version.version, "saved": version.saved, "is_current": version.is_current, "remark": version.remark, "parameters": version.parameters, "metrics": version.metrics, "created_at": version.created_at, "updated_at": version.updated_at}


def create_factor_draft(session: Session, project: FactorProject, user_id: int, parameters: dict[str, Any]) -> FactorVersion:
    version_number = (session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id)) or 0) + 1
    workspace = WorkflowWorkspace(user_id=user_id, application="factor")
    session.add(workspace)
    session.flush()
    version = FactorVersion(project_id=project.id, workflow_workspace_id=workspace.id, version=version_number, saved=False, is_current=True, parameters=parameters)
    session.add(version)
    session.flush()
    return version


def validate_metric_dimensions(parameters: dict[str, Any], metrics: dict[str, Any]) -> None:
    factors = set(parameters.get("factor_columns") or [])
    returns = set(parameters.get("return_columns") or [])
    if set(metrics) != factors:
        raise ValueError(f"metrics 因子必须与 factor_columns 一致: {sorted(factors)}")
    for factor, values in metrics.items():
        if set(values) != returns:
            raise ValueError(f"metrics[{factor!r}] 收益列必须与 return_columns 一致: {sorted(returns)}")


def submit_factor_batch(session: Session, user_id: int, project_id: int, items: Sequence[BatchRunItem[FactorAnalysisParameters]]) -> list[dict[str, Any]]:
    project = session.scalar(select(FactorProject).where(FactorProject.id == project_id, FactorProject.user_id == user_id).with_for_update())
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
    workspace_ids, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(
        session,
        FactorVersion,
        user_id,
        project.id,
        {item.client_id for item in items},
    )
    next_version = (session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id)) or 0) + 1
    new_workspace_ids: list[int] = []
    for item in items:
        if item.client_id in workspace_ids:
            continue
        run = WorkflowWorkspace(user_id=user_id, application="factor")
        session.add(run)
        session.flush()
        version = FactorVersion(
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


def finalize_factor_auto_save_workspace(session: Session, run: WorkflowWorkspace) -> None:
    attempt = require_current_workflow_attempt(session, run.id)
    metadata = auto_save_metadata(attempt)
    if metadata is None or attempt.submission_state != "AUTO_SAVE_PENDING":
        return
    workflow = session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt.id))
    if workflow is None or workflow.state != "SUCCESS":
        return
    try:
        version = session.scalar(select(FactorVersion).where(FactorVersion.workflow_workspace_id == run.id).with_for_update())
        if version is None:
            raise RuntimeError("批量因子工作空间缺少版本记录")
        project_id = metadata.get("project_id")
        if not isinstance(project_id, int) or project_id != version.project_id:
            raise ValueError("自动保存版本的项目标识无效")
        project = session.scalar(select(FactorProject).where(FactorProject.id == project_id).with_for_update())
        if project is None:
            raise FileNotFoundError(f"因子项目不存在: {project_id}")
        save_factor_version(session, project, version, run, attempt, workflow, str(metadata.get("remark") or ""))
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


def factor_metrics(parameters: dict[str, Any], information: pd.DataFrame, groups: pd.DataFrame) -> dict[str, Any]:
    if "time" not in groups:
        raise ValueError("因子分组收益结果缺少列: time")
    result: dict[str, Any] = {}
    count_groups = int(parameters["n_groups"])
    for factor in parameters["factor_columns"]:
        result[factor] = {}
        for return_column in parameters["return_columns"]:
            ic = numeric_series(information, f"{factor}_{return_column}_ic")
            rank_ic = numeric_series(information, f"{factor}_{return_column}_rank_ic")
            low_column = f"{factor}_{return_column}_group0"
            high_column = f"{factor}_{return_column}_group{count_groups - 1}"
            low = numeric_series(groups, low_column)
            high = numeric_series(groups, high_column)
            returns = pd.DataFrame({"time": groups["time"], "value": high - low}).dropna().sort_values("time")["value"]
            observations = int(ic.count())
            ic_std = clean_number(ic.std(ddof=1))
            rank_std = clean_number(rank_ic.std(ddof=1))
            annual_volatility = clean_number(returns.std(ddof=0) * sqrt(252))
            growth, maximum_drawdown = return_growth(returns)
            annual_return = clean_number(growth ** (252 / len(returns)) - 1) if growth is not None and len(returns) else None
            result[factor][return_column] = {
                "observations": observations,
                "ic_mean": clean_number(ic.mean()),
                "ic_std": ic_std,
                "ic_ir": ratio(clean_number(ic.mean()), ic_std),
                "ic_positive_ratio": clean_number((ic > 0).sum() / observations) if observations else None,
                "rank_ic_mean": clean_number(rank_ic.mean()),
                "rank_ic_std": rank_std,
                "rank_ic_ir": ratio(clean_number(rank_ic.mean()), rank_std),
                "rank_ic_positive_ratio": clean_number((rank_ic > 0).sum() / rank_ic.count()) if rank_ic.count() else None,
                "long_short_cumulative_return": clean_number(growth - 1) if growth is not None else None,
                "long_short_annual_return": annual_return,
                "long_short_annual_volatility": annual_volatility,
                "long_short_sharpe": ratio(annual_return, annual_volatility),
                "long_short_max_drawdown": maximum_drawdown,
            }
    return result


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise ValueError(f"因子结果缺少列: {column}")
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def return_growth(returns: pd.Series) -> tuple[float | None, float | None]:
    if returns.empty:
        return None, None
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        wealth = np.exp(np.log1p(returns.to_numpy(dtype=float)).cumsum())
    growth = clean_number(wealth[-1])
    if growth is None:
        return None, None
    peaks = np.maximum.accumulate(np.concatenate(([1.0], wealth)))[1:]
    drawdowns = wealth / peaks - 1
    return growth, clean_number(abs(np.nanmin(drawdowns)))


def clean_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return None if numerator is None or denominator is None or denominator == 0 else clean_number(numerator / denominator)
