"""Result-file access keyed by DolphinScheduler workflow instance ID."""

from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from stat import S_ISREG
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Response
from fastapi.responses import FileResponse, RedirectResponse
import pandas as pd
from pydantic import BaseModel
from runtime.utils.storage import (
    PARQUET_CONTENT_TYPE,
    ObjectStorage,
    ObjectStorageConfigurationError,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.apps.workflows.artifacts import (
    uses_cloud_output,
    workspace_output_directory,
    workspace_output_prefix,
)
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace


class ResultFile[Name: str](BaseModel):
    name: Name
    filename: str
    size: int
    modified_at: datetime


def owned_result_workspace(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
) -> tuple[WorkflowWorkspace, WorkflowAttempt]:
    row = session.execute(
        select(WorkflowWorkspace, WorkflowAttempt, WorkflowInstance)
        .join(WorkflowAttempt, WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id)
        .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
        .where(
            WorkflowInstance.workflow_instance_id == workflow_instance_id,
            WorkflowWorkspace.user_id == user_id,
            WorkflowWorkspace.application == application,
        )
    ).one_or_none()
    if row is None:
        raise FileNotFoundError(f"工作流实例不存在: {workflow_instance_id}")
    workspace, attempt, workflow = row
    if not attempt.is_current:
        raise RuntimeError(
            f"工作流 {workflow_instance_id} 不是当前实例，结果目录已由后续运行接管"
        )
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流 {workflow_instance_id} 当前状态为 {workflow.state}，成功后才能获取结果")
    return workspace, attempt


def result_files(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
    output_files: dict[str, str],
    optional_outputs: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
    optional_outputs = optional_outputs or {}
    if uses_cloud_output(workspace.application):
        storage, output_key = cloud_storage(workspace.application, workspace.workspace_key)
        try:
            files: list[dict[str, Any]] = []
            for name in attempt.requested_outputs:
                try:
                    files.append(
                        cloud_result_file(
                            storage,
                            output_key,
                            name,
                            result_filename(workflow_instance_id, name, output_files),
                        )
                    )
                except ClientError as error:
                    if name in optional_outputs and is_missing_object(error):
                        continue
                    raise
            return files
        except (BotoCoreError, ClientError) as error:
            raise OSError(
                f"工作流 {workflow_instance_id} 无法读取对象存储结果: {error}"
            ) from error
        finally:
            storage.close()

    output_dir = workspace_output_directory(workspace.application, workspace.workspace_key)
    files = []
    for name in attempt.requested_outputs:
        try:
            files.append(
                local_result_file(
                    workflow_instance_id,
                    output_dir,
                    name,
                    result_filename(workflow_instance_id, name, output_files),
                )
            )
        except FileNotFoundError:
            if name in optional_outputs:
                continue
            raise
    return files


def result_response(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    name: str,
    application: str,
    output_files: dict[str, str],
    optional_outputs: Mapping[str, str] | None = None,
) -> Response:
    if name not in output_files:
        raise FileNotFoundError(f"未知结果: {name}")
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
    optional_outputs = optional_outputs or {}
    if name not in attempt.requested_outputs:
        raise FileNotFoundError(f"工作流未请求结果: {name}")
    filename = output_files[name]
    if uses_cloud_output(workspace.application):
        storage, output_key = cloud_storage(workspace.application, workspace.workspace_key)
        key = f"{output_key}/{filename}"
        try:
            storage.object_info(key)
            url = storage.download_url(key)
        except ClientError as error:
            if name in optional_outputs and is_missing_object(error):
                raise FileNotFoundError(optional_outputs[name]) from error
            raise OSError(f"工作流 {workflow_instance_id} 成功但无法读取结果 {name}: {error}") from error
        except BotoCoreError as error:
            raise OSError(f"工作流 {workflow_instance_id} 成功但无法读取结果 {name}: {error}") from error
        finally:
            storage.close()
        return RedirectResponse(url, headers={"Cache-Control": "private, no-store"})

    output_dir = workspace_output_directory(workspace.application, workspace.workspace_key)
    try:
        path = local_result_path(
            workflow_instance_id,
            output_dir,
            name,
            filename,
        )
    except FileNotFoundError as error:
        if name in optional_outputs:
            raise FileNotFoundError(optional_outputs[name]) from error
        raise
    return FileResponse(path, filename=filename, media_type=PARQUET_CONTENT_TYPE)


def result_dataframe(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
    name: str,
    output_files: dict[str, str],
) -> pd.DataFrame:
    """读取一个已成功工作流的 Parquet 结果，用于后端生成版本摘要。"""
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
    if name not in attempt.requested_outputs:
        raise FileNotFoundError(f"工作流未请求结果: {name}")
    return read_result_dataframe(workspace.application, workspace.workspace_key, workflow_instance_id, name, output_files)


def read_result_dataframe(application: str, workspace_key: str, workflow_instance_id: int, name: str, output_files: dict[str, str]) -> pd.DataFrame:
    """从工作流结果目录读取 Parquet，不执行数据库查询。"""
    filename = result_filename(workflow_instance_id, name, output_files)
    if uses_cloud_output(application):
        storage, output_key = cloud_storage(application, workspace_key)
        try:
            response = storage.client.get_object(Bucket=storage.bucket, Key=f"{output_key}/{filename}")
            return pd.read_parquet(BytesIO(response["Body"].read()))
        except (BotoCoreError, ClientError) as error:
            raise OSError(f"工作流 {workflow_instance_id} 无法读取对象存储结果 {name}: {error}") from error
        finally:
            storage.close()
    path = workspace_output_directory(application, workspace_key) / filename
    if not path.is_file():
        raise OSError(f"工作流 {workflow_instance_id} 成功但缺少结果: {name}")
    return pd.read_parquet(path)


def delete_result_objects(application: str, workspace_key: str) -> None:
    """删除云端结果目录；本地结果由所属工作目录统一清理。"""
    if not uses_cloud_output(application):
        return
    delete_cloud_result_objects(application, workspace_key)


def delete_cloud_result_objects(application: str, workspace_key: str) -> None:
    """删除当前对象存储中的指定 workspace 输出前缀。"""
    storage, output_key = cloud_storage(application, workspace_key)
    try:
        storage.delete_prefix(output_key)
    except (BotoCoreError, ClientError, ObjectStorageConfigurationError) as error:
        raise OSError(
            f"无法清理对象存储 workspace {application}/{workspace_key}: {error}"
        ) from error
    finally:
        storage.close()


def result_filename(
    workflow_instance_id: int,
    name: str,
    output_files: dict[str, str],
) -> str:
    filename = output_files.get(name)
    if filename is None:
        raise OSError(f"工作流 {workflow_instance_id} 包含未知结果: {name}")
    return filename


def local_result_file(
    workflow_instance_id: int,
    output_dir: Path,
    name: str,
    filename: str,
) -> dict[str, Any]:
    path = local_result_path(
        workflow_instance_id,
        output_dir,
        name,
        filename,
    )
    file_stat = path.stat()
    return {
        "name": name,
        "filename": filename,
        "size": file_stat.st_size,
        "modified_at": datetime.fromtimestamp(file_stat.st_mtime, UTC),
    }


def local_result_path(
    workflow_instance_id: int,
    output_dir: Path,
    name: str,
    filename: str,
) -> Path:
    """返回经过目录边界和普通文件校验的本地结果路径。"""
    path = (output_dir / filename).resolve()
    if path.parent != output_dir:
        raise OSError(f"工作流 {workflow_instance_id} 的结果路径越界: {name}")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"工作流 {workflow_instance_id} 成功但缺少结果: {name}"
        ) from error
    if not S_ISREG(mode):
        raise OSError(f"工作流 {workflow_instance_id} 的结果不是普通文件: {name}")
    return path


def cloud_result_file(
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


def is_missing_object(error: ClientError) -> bool:
    """判断 S3 兼容接口是否明确返回对象不存在。"""
    response = error.response
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def cloud_storage(application: str, workspace_key: str) -> tuple[ObjectStorage, str]:
    storage: ObjectStorage | None = None
    try:
        storage = ObjectStorage.from_env()
        return storage, storage.object_key(
            workspace_output_prefix(application, workspace_key)
        )
    except ObjectStorageConfigurationError as error:
        if storage is not None:
            storage.close()
        raise OSError(str(error)) from error
