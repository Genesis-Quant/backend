"""Factor workflow submission, research projects, versions, and results."""

from collections.abc import Sequence
from math import isfinite, sqrt
from typing import Any

from fastapi import Response
import numpy as np
import pandas as pd
from runtime.apps.factor.schema import FactorAnalysisParameters
from sqlalchemy import Float, and_, delete, func, literal, or_, select
from sqlalchemy.orm import Session

from core.apps.factor.models import FactorProject, FactorVersion
from core.apps.factor.schemas import FactorProjectSortField
from core.apps.schemas import BatchRunItem, SortOrder
from core.apps.workflows.artifacts import FACTOR_OUTPUT_FILES
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    BATCH_PENDING_STATE,
    WORKSPACE_TERMINAL_STATES,
    WorkflowExecutionService,
    auto_save_metadata,
    auto_save_workspaces,
    assign_auto_saved_version_number,
    create_workflow_attempt,
    current_workflow_attempt,
    current_workflow_instance,
    finalize_auto_save_workspaces_now,
    record_event,
    remove_workspace_artifacts,
    require_current_workflow_attempt,
    resolve_workspace_artifacts,
    submit_workspaces_now,
    workflow_attempt_state,
    workflow_workspace_state,
)
from core.utils.projects import list_project_summaries
from core.utils.results import (
    ensure_successful_workflow_outputs,
    result_dataframe,
    result_files,
    result_response,
)
from core.utils.time import utc_now
from core.utils.dsl_source import (
    FactorAnalysisApplicationRequest,
    compile_application_payload,
)

OUTPUT_FILES = FACTOR_OUTPUT_FILES
PROJECT_OUTPUTS = [
    "execution_statistics",
    "information_coefficient",
    "group_returns",
    "group_turnover",
]


def factor_result_files(session: Session, user_id: int, workflow_instance_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, workflow_instance_id, "factor", OUTPUT_FILES)


def factor_result_response(session: Session, user_id: int, workflow_instance_id: int, name: str) -> Response:
    return result_response(session, user_id, workflow_instance_id, name, "factor", OUTPUT_FILES)


def list_factor_projects(
    session: Session,
    user_id: int,
    page: int,
    page_size: int,
    search: str | None,
    sort_by: FactorProjectSortField,
    sort_order: SortOrder,
) -> dict[str, Any]:
    base_statement = select(FactorProject).where(FactorProject.user_id == user_id)
    database_sort_columns: dict[str, Any] = {
        "id": FactorProject.id,
        "title": func.lower(FactorProject.title),
        "updated_at": FactorProject.updated_at,
    }
    if sort_by not in database_sort_columns:
        latest = factor_project_sort_value(session, user_id, sort_by)
        base_statement = base_statement.outerjoin(
            latest,
            latest.c.project_id == FactorProject.id,
        )
        database_sort_columns[sort_by] = latest.c.sort_value
    return list_project_summaries(
        session,
        base_statement,
        FactorProject,
        project_summaries,
        page,
        page_size,
        search,
        sort_by,
        sort_order,
        database_sort_columns,
        {},
    )


def factor_project_sort_value(
    session: Session,
    user_id: int,
    sort_by: FactorProjectSortField,
) -> Any:
    """Expose one persisted value from the latest saved version for SQL sorting."""
    owned_project_ids = select(FactorProject.id).where(
        FactorProject.user_id == user_id
    )
    latest_numbers = (
        select(
            FactorVersion.project_id,
            func.max(FactorVersion.version).label("version"),
        )
        .where(
            FactorVersion.project_id.in_(owned_project_ids),
            FactorVersion.saved.is_(True),
        )
        .group_by(FactorVersion.project_id)
        .subquery()
    )
    if sort_by == "latest_version":
        sort_value = FactorVersion.version
    else:
        factor_name = FactorVersion.parameters["factor_columns"][0].as_string()
        return_name = FactorVersion.parameters["return_columns"][0].as_string()
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            sort_value = func.jsonb_extract_path_text(
                FactorVersion.metrics,
                factor_name,
                return_name,
                literal(sort_by),
            ).cast(Float)
        elif dialect == "sqlite":
            sort_value = func.json_extract(
                FactorVersion.metrics,
                literal('$."')
                + factor_name
                + literal('"."')
                + return_name
                + literal(f'"."{sort_by}"'),
            ).cast(Float)
        else:
            raise RuntimeError(f"不支持使用 {dialect} 排序因子项目摘要")
    return (
        select(
            FactorVersion.project_id.label("project_id"),
            sort_value.label("sort_value"),
        )
        .join(
            latest_numbers,
            and_(
                FactorVersion.project_id == latest_numbers.c.project_id,
                FactorVersion.version == latest_numbers.c.version,
            ),
        )
        .subquery()
    )


def create_factor_project(
    session: Session,
    user_id: int,
    title: str,
    parameters: FactorAnalysisApplicationRequest,
) -> dict[str, Any]:
    project = FactorProject(user_id=user_id, title=title)
    session.add(project)
    session.flush()
    create_factor_draft(
        session,
        project,
        user_id,
        parameters.stored_payload(),
    )
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


def submit_project_analysis(
    session: Session,
    user_id: int,
    project_id: int,
    payload: dict[str, Any],
) -> WorkflowWorkspace:
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
    if workflow.state == "SUCCESS" and not ensure_successful_workflow_outputs(
        run,
        attempt,
        workflow,
    ):
        session.commit()
    state = workflow_attempt_state(attempt, workflow)
    if state != "SUCCESS":
        raise RuntimeError(f"工作流状态为 {state}，成功后才能保存版本")
    save_factor_version(session, project, version, run, attempt, workflow, remark)
    create_factor_draft(session, project, user_id, attempt.input_json)
    session.commit()
    return serialize_version(session, version)


def save_factor_version(session: Session, project: FactorProject, version: FactorVersion, run: WorkflowWorkspace, attempt: WorkflowAttempt, workflow: WorkflowInstance, remark: str) -> FactorVersion:
    if version.saved:
        return version
    validated = FactorAnalysisParameters.model_validate(
        compile_application_payload("factor", attempt.input_json)
    )
    parameters = validated.model_dump(mode="json")
    information = result_dataframe(session, run.user_id, workflow.workflow_instance_id, "factor", "information_coefficient", OUTPUT_FILES)
    groups = result_dataframe(session, run.user_id, workflow.workflow_instance_id, "factor", "group_returns", OUTPUT_FILES)
    turnover = result_dataframe(
        session,
        run.user_id,
        workflow.workflow_instance_id,
        "factor",
        "group_turnover",
        OUTPUT_FILES,
    )
    metrics = factor_metrics(validated, information, groups, turnover)
    validate_metric_dimensions(parameters, metrics)
    if version.version is None:
        assign_auto_saved_version_number(session, version)
    version.workflow_instance_id = workflow.workflow_instance_id
    version.saved = True
    version.is_current = False
    version.remark = remark
    version.parameters = attempt.input_json
    version.metrics = metrics
    version.updated_at = utc_now()
    project.updated_at = utc_now()
    session.flush()
    return version


def list_factor_versions(session: Session, user_id: int, project_id: int) -> list[dict[str, Any]]:
    project = owned_project(session, user_id, project_id)
    rows = session.execute(
        select(FactorVersion.id, FactorVersion.version, FactorVersion.saved, FactorVersion.is_current, FactorVersion.remark, FactorVersion.workflow_instance_id, FactorVersion.created_at)
        .where(
            FactorVersion.project_id == project.id,
            or_(FactorVersion.saved.is_(True), FactorVersion.is_current.is_(True)),
        )
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
    project = session.scalar(
        select(FactorProject)
        .where(FactorProject.id == project_id, FactorProject.user_id == user_id)
        .with_for_update()
    )
    if project is None:
        raise FileNotFoundError(f"因子项目不存在: {project_id}")
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
        select(
            FactorVersion.project_id,
            FactorVersion.version,
            FactorVersion.metrics,
            FactorVersion.parameters,
        ).join(
            latest_numbers,
            and_(FactorVersion.project_id == latest_numbers.c.project_id, FactorVersion.version == latest_numbers.c.version),
        )
    ).all()
    latest_by_project = {
        project_id: (version, selected_metric(metrics, parameters))
        for project_id, version, metrics, parameters in latest_versions
    }
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


def selected_metric(
    metrics: dict[str, Any] | None,
    parameters: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the persisted metric shown for the first configured factor/return pair."""
    if metrics is None or parameters is None:
        return None
    factor_columns = parameters.get("factor_columns")
    return_columns = parameters.get("return_columns")
    if not factor_columns or not return_columns:
        return None
    returns = metrics.get(factor_columns[0])
    metric = returns.get(return_columns[0]) if isinstance(returns, dict) else None
    return metric if isinstance(metric, dict) else None


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
        workflow = current_workflow_instance(session, version.workflow_workspace_id)
        workflow_instance_id = workflow.workflow_instance_id if workflow is not None else None
    return {"id": version.id, "project_id": version.project_id, "workflow_workspace_id": version.workflow_workspace_id, "workflow_instance_id": workflow_instance_id, "version": version.version, "saved": version.saved, "is_current": version.is_current, "remark": version.remark, "parameters": version.parameters, "metrics": version.metrics, "created_at": version.created_at, "updated_at": version.updated_at}


def create_factor_draft(session: Session, project: FactorProject, user_id: int, parameters: dict[str, Any]) -> FactorVersion:
    version_number = (session.scalar(select(func.max(FactorVersion.version)).where(FactorVersion.project_id == project.id, FactorVersion.saved.is_(True))) or 0) + 1
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


def submit_factor_batch(
    session: Session,
    user_id: int,
    project_id: int,
    items: Sequence[BatchRunItem[FactorAnalysisApplicationRequest]],
) -> list[dict[str, Any]]:
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
    new_workspace_ids: list[int] = []
    for item in items:
        if item.client_id in workspace_ids:
            continue
        run = WorkflowWorkspace(user_id=user_id, application="factor")
        session.add(run)
        session.flush()
        stored_payload = item.parameters.stored_payload()
        version = FactorVersion(
            project_id=project.id,
            workflow_workspace_id=run.id,
            version=None,
            saved=False,
            is_current=False,
            remark=item.remark,
            parameters=stored_payload,
        )
        session.add(version)
        attempt = create_workflow_attempt(
            session,
            run,
            stored_payload,
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


def factor_metrics(
    parameters: FactorAnalysisParameters | dict[str, Any],
    information: pd.DataFrame,
    groups: pd.DataFrame,
    turnover: pd.DataFrame,
) -> dict[str, Any]:
    if "time" not in groups:
        raise ValueError("因子分组收益结果缺少列: time")
    validated = FactorAnalysisParameters.model_validate(parameters)
    result: dict[str, Any] = {}
    count_groups = validated.n_groups
    for factor in validated.factor_columns:
        result[factor] = {}
        for return_column in validated.return_columns:
            ic = numeric_series(information, f"{factor}_{return_column}_ic")
            rank_ic = numeric_series(information, f"{factor}_{return_column}_rank_ic")
            extreme_low = f"{factor}_{return_column}_bottom"
            extreme_high = f"{factor}_{return_column}_top"
            low = numeric_series(groups, extreme_low)
            high = numeric_series(groups, extreme_high)
            returns = pd.DataFrame({"time": groups["time"], "value": high - low}).dropna().sort_values("time")["value"]
            return_spec = validated.return_specs[return_column]
            return_kind = return_spec.kind
            return_periods = return_spec.periods
            average_turnover = factor_average_turnover(
                turnover,
                factor,
                return_periods,
                count_groups,
            )
            observations = int(ic.count())
            ic_std = clean_number(ic.std(ddof=1))
            rank_std = clean_number(rank_ic.std(ddof=1))
            if return_periods == 1:
                realized_returns = (
                    pd.Series(np.expm1(returns.to_numpy(dtype=float)))
                    if return_kind == "log"
                    else returns
                )
                annual_volatility = clean_number(
                    realized_returns.std(ddof=0) * sqrt(252)
                )
                growth, maximum_drawdown = return_growth(
                    returns,
                    return_kind,
                )
                annual_return = (
                    clean_number(growth ** (252 / len(returns)) - 1)
                    if growth is not None and len(returns)
                    else None
                )
            else:
                growth = None
                maximum_drawdown = None
                annual_volatility = None
                annual_return = None
            result[factor][return_column] = {
                "return_kind": return_kind,
                "return_periods": return_periods,
                "compoundable": return_periods == 1,
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
                "average_turnover": average_turnover,
            }
    return result


def factor_average_turnover(
    turnover: pd.DataFrame,
    factor: str,
    periods: int,
    count_groups: int,
) -> float | None:
    """Return the equally weighted mean of the available portfolio turnovers."""
    required = {"factor", "periods"}
    missing = required.difference(turnover.columns)
    if missing:
        raise ValueError(f"因子换手率结果缺少列: {', '.join(sorted(missing))}")
    group_columns = [f"group{group_id}" for group_id in range(count_groups)]
    portfolio_columns = ["bottom", *group_columns, "top"]
    missing_portfolios = set(portfolio_columns).difference(turnover.columns)
    if missing_portfolios:
        raise ValueError(
            "因子换手率结果缺少组合列: "
            f"{', '.join(sorted(missing_portfolios))}"
        )
    period_values = pd.to_numeric(turnover["periods"], errors="coerce")
    selected = turnover.loc[
        (turnover["factor"].astype(str) == factor) & (period_values == periods),
        portfolio_columns,
    ].apply(pd.to_numeric, errors="coerce")
    if selected.empty:
        return None
    return clean_number(selected.mean(axis=0, skipna=True).mean(skipna=True))


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise ValueError(f"因子结果缺少列: {column}")
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def return_growth(
    returns: pd.Series,
    return_kind: str,
) -> tuple[float | None, float | None]:
    if returns.empty:
        return None, None
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        values = returns.to_numpy(dtype=float)
        wealth = (
            np.exp(values.cumsum())
            if return_kind == "log"
            else np.exp(np.log1p(values).cumsum())
        )
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
