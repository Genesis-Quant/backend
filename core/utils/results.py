"""Result-file access keyed by DolphinScheduler workflow instance ID."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.apps.workflows.models import WorkflowInstance, WorkflowRun


class ResultFile(BaseModel):
    name: str
    filename: str
    size: int
    modified_at: datetime


def owned_result_run(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
) -> WorkflowRun:
    run = session.scalar(
        select(WorkflowRun)
        .join(WorkflowInstance, WorkflowInstance.workflow_run_id == WorkflowRun.id)
        .where(
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowRun.user_id == user_id,
            WorkflowRun.application == application,
        )
    )
    if run is None:
        raise FileNotFoundError(f"工作流实例不存在: {workflow_instance_id}")
    workflow = session.get(WorkflowInstance, workflow_instance_id)
    if workflow is not None and not workflow.is_current:
        raise RuntimeError(
            f"工作流 {workflow_instance_id} 不是当前实例，结果目录已由后续运行接管"
        )
    if workflow is None or workflow.state != "SUCCESS":
        state = workflow.state if workflow is not None else "UNKNOWN"
        raise RuntimeError(f"工作流 {workflow_instance_id} 当前状态为 {state}，成功后才能获取结果")
    return run


def result_files(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
    output_files: dict[str, str],
) -> list[dict[str, Any]]:
    run = owned_result_run(session, user_id, workflow_instance_id, application)
    if not run.output_dir:
        raise OSError(f"工作流 {workflow_instance_id} 成功但未记录结果目录")
    output_dir = Path(run.output_dir).resolve()
    results = []
    for name in run.requested_outputs:
        filename = output_files.get(name)
        if filename is None:
            raise OSError(f"工作流 {workflow_instance_id} 包含未知结果: {name}")
        path = (output_dir / filename).resolve()
        if path.parent != output_dir or not path.is_file():
            raise OSError(f"工作流 {workflow_instance_id} 成功但缺少结果: {name}")
        stat = path.stat()
        results.append({
            "name": name,
            "filename": filename,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC),
        })
    return results


def result_path(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    name: str,
    application: str,
    output_files: dict[str, str],
) -> Path:
    if name not in output_files:
        raise FileNotFoundError(f"未知结果: {name}")
    run = owned_result_run(session, user_id, workflow_instance_id, application)
    if name not in run.requested_outputs:
        raise FileNotFoundError(f"工作流未请求结果: {name}")
    if not run.output_dir:
        raise OSError(f"工作流 {workflow_instance_id} 成功但未记录结果目录")
    output_dir = Path(run.output_dir).resolve()
    path = (output_dir / output_files[name]).resolve()
    if path.parent != output_dir or not path.is_file():
        raise OSError(f"工作流 {workflow_instance_id} 成功但缺少结果: {name}")
    return path
