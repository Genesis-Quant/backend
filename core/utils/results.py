"""Result-file access keyed by DolphinScheduler workflow instance ID."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from runtime.utils.storage import (
    ObjectStorage,
    ObjectStorageConfigurationError,
    PARQUET_CONTENT_TYPE,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.apps.workflows.models import WorkflowInstance, WorkflowRun


class ResultFile(BaseModel):
    name: str
    filename: str
    size: int
    modified_at: datetime


def cloud_output_location(application: str, workspace_key: str) -> tuple[str, str]:
    """返回 Runtime 使用的对象前缀及持久化到任务记录的 S3 URI。"""
    prefix = f"{application}/{workspace_key}/output"
    try:
        with ObjectStorage.from_env() as storage:
            return prefix, storage.uri(storage.object_key(prefix))
    except ObjectStorageConfigurationError as error:
        raise OSError(str(error)) from error


def is_cloud_output(output_dir: str | None) -> bool:
    return bool(output_dir and output_dir.startswith("s3://"))


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
    if is_cloud_output(run.output_dir):
        storage, output_key = _cloud_storage(run.output_dir)
        try:
            return [
                _cloud_result_file(
                    storage,
                    output_key,
                    name,
                    _result_filename(workflow_instance_id, name, output_files),
                )
                for name in run.requested_outputs
            ]
        except (BotoCoreError, ClientError) as error:
            raise OSError(f"工作流 {workflow_instance_id} 无法读取对象存储结果: {error}") from error
        finally:
            storage.close()

    output_dir = Path(run.output_dir).resolve()
    return [
        _local_result_file(
            workflow_instance_id,
            output_dir,
            name,
            _result_filename(workflow_instance_id, name, output_files),
        )
        for name in run.requested_outputs
    ]


def result_response(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    name: str,
    application: str,
    output_files: dict[str, str],
) -> Response:
    if name not in output_files:
        raise FileNotFoundError(f"未知结果: {name}")
    run = owned_result_run(session, user_id, workflow_instance_id, application)
    if name not in run.requested_outputs:
        raise FileNotFoundError(f"工作流未请求结果: {name}")
    if not run.output_dir:
        raise OSError(f"工作流 {workflow_instance_id} 成功但未记录结果目录")
    filename = output_files[name]
    if is_cloud_output(run.output_dir):
        storage, output_key = _cloud_storage(run.output_dir)
        key = f"{output_key}/{filename}"
        try:
            storage.object_info(key)
            url = storage.download_url(key)
        except (BotoCoreError, ClientError) as error:
            raise OSError(f"工作流 {workflow_instance_id} 成功但无法读取结果 {name}: {error}") from error
        finally:
            storage.close()
        return RedirectResponse(url, headers={"Cache-Control": "private, no-store"})

    output_dir = Path(run.output_dir).resolve()
    path = (output_dir / filename).resolve()
    if path.parent != output_dir or not path.is_file():
        raise OSError(f"工作流 {workflow_instance_id} 成功但缺少结果: {name}")
    return FileResponse(path, filename=filename, media_type=PARQUET_CONTENT_TYPE)


def delete_result_objects(output_dir: str | None) -> None:
    """删除云端结果目录；本地结果由所属工作目录统一清理。"""
    if not is_cloud_output(output_dir):
        return
    storage, output_key = _cloud_storage(output_dir)
    try:
        storage.delete_prefix(output_key)
    except (BotoCoreError, ClientError, ObjectStorageConfigurationError) as error:
        raise OSError(f"无法清理对象存储结果 {output_dir}: {error}") from error
    finally:
        storage.close()


def _result_filename(
    workflow_instance_id: int,
    name: str,
    output_files: dict[str, str],
) -> str:
    filename = output_files.get(name)
    if filename is None:
        raise OSError(f"工作流 {workflow_instance_id} 包含未知结果: {name}")
    return filename


def _local_result_file(
    workflow_instance_id: int,
    output_dir: Path,
    name: str,
    filename: str,
) -> dict[str, Any]:
    path = (output_dir / filename).resolve()
    if path.parent != output_dir or not path.is_file():
        raise OSError(f"工作流 {workflow_instance_id} 成功但缺少结果: {name}")
    stat = path.stat()
    return {
        "name": name,
        "filename": filename,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC),
    }


def _cloud_result_file(
    storage: ObjectStorage,
    output_key: str,
    name: str,
    filename: str,
) -> dict[str, Any]:
    info = storage.object_info(f"{output_key}/{filename}")
    return {
        "name": name,
        "filename": filename,
        "size": info.size,
        "modified_at": info.modified_at,
    }


def _cloud_storage(output_dir: str) -> tuple[ObjectStorage, str]:
    storage: ObjectStorage | None = None
    try:
        storage = ObjectStorage.from_env()
        return storage, storage.key_from_uri(output_dir)
    except ObjectStorageConfigurationError as error:
        if storage is not None:
            storage.close()
        raise OSError(str(error)) from error
