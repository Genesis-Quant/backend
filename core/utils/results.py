"""Result-file access keyed by DolphinScheduler workflow instance ID."""

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
    workflow_output_files,
    workspace_output_directory,
    workspace_output_prefix,
)
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.utils.time import utc_now

RESULT_FAILED_STATE = "RESULT_FAILED"
OUTPUTS_VALIDATED_EVENT = "WORKFLOW_OUTPUTS_VALIDATED"
OUTPUT_VALIDATION_FAILED_EVENT = "WORKFLOW_OUTPUT_VALIDATION_FAILED"


class ResultFile[Name: str](BaseModel):
    name: Name
    filename: str
    size: int
    modified_at: datetime


class WorkflowOutputValidationError(ValueError):
    """表示工作流输出确定性地缺失、为空或不完整。"""


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
    if attempt.submission_state == "RESULT_FAILED":
        raise RuntimeError(
            f"工作流 {workflow_instance_id} 的结果校验失败: "
            f"{attempt.error or '缺少必需结果'}"
        )
    if workflow.state != "SUCCESS":
        raise RuntimeError(f"工作流 {workflow_instance_id} 当前状态为 {workflow.state}，成功后才能获取结果")
    if attempt.requested_outputs:
        validation_state = workflow_output_validation_state(attempt, workflow)
        validated = ensure_successful_workflow_outputs(
            workspace,
            attempt,
            workflow,
        )
        if validation_state is None:
            session.commit()
        if not validated:
            raise RuntimeError(
                f"工作流 {workflow_instance_id} 的结果校验失败: "
                f"{attempt.error or '缺少必需结果'}"
            )
    return workspace, attempt


def result_files(
    session: Session,
    user_id: int,
    workflow_instance_id: int,
    application: str,
    output_files: dict[str, str],
) -> list[dict[str, Any]]:
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
    if uses_cloud_output(workspace.application):
        storage, output_key = cloud_storage(workspace.application, workspace.workspace_key)
        try:
            files: list[dict[str, Any]] = []
            for name in attempt.requested_outputs:
                files.append(
                    cloud_result_file(
                        storage,
                        output_key,
                        name,
                        result_filename(workflow_instance_id, name, output_files),
                    )
                )
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
        files.append(
            local_result_file(
                workflow_instance_id,
                output_dir,
                name,
                result_filename(workflow_instance_id, name, output_files),
            )
        )
    return files


def validate_required_result_files(
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    workflow_instance_id: int,
) -> None:
    """Ensure every requested output exists before exposing business success."""
    output_files = workflow_output_files(
        workspace.application,
        attempt.requested_outputs,
    )
    if not output_files:
        return
    missing: list[str] = []
    empty: list[str] = []
    invalid: list[str] = []
    if uses_cloud_output(workspace.application):
        storage, output_key = cloud_storage(
            workspace.application,
            workspace.workspace_key,
        )
        try:
            for name, filename in output_files.items():
                key = f"{output_key}/{filename}"
                try:
                    information = storage.object_info(key)
                except ClientError as error:
                    if is_missing_object(error):
                        missing.append(name)
                        continue
                    raise
                if information.size <= 0:
                    empty.append(name)
                elif information.size < 4:
                    invalid.append(name)
                else:
                    response = storage.client.get_object(
                        Bucket=storage.bucket,
                        Key=key,
                        Range=f"bytes={information.size - 4}-{information.size - 1}",
                    )
                    try:
                        if response["Body"].read() != b"PAR1":
                            invalid.append(name)
                    finally:
                        response["Body"].close()
        except (BotoCoreError, ClientError) as error:
            raise OSError(
                f"工作流 {workflow_instance_id} 无法校验对象存储结果: {error}"
            ) from error
        finally:
            storage.close()
    else:
        output_directory = workspace_output_directory(
            workspace.application,
            workspace.workspace_key,
        )
        for name, filename in output_files.items():
            try:
                path = local_result_path(
                    workflow_instance_id,
                    output_directory,
                    name,
                    filename,
                )
            except FileNotFoundError:
                missing.append(name)
                continue
            size = path.stat().st_size
            if size <= 0:
                empty.append(name)
                continue
            if size < 4:
                invalid.append(name)
                continue
            with path.open("rb") as file:
                file.seek(-4, 2)
                if file.read(4) != b"PAR1":
                    invalid.append(name)
    problems = []
    if missing:
        problems.append(f"缺少必需结果: {', '.join(missing)}")
    if empty:
        problems.append(f"结果文件为空: {', '.join(empty)}")
    if invalid:
        problems.append(f"结果文件不是完整 Parquet: {', '.join(invalid)}")
    if problems:
        raise WorkflowOutputValidationError(
            f"工作流 {workflow_instance_id} " + "；".join(problems)
        )


def workflow_output_validation_state(
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> str | None:
    """返回当前工作流实例已记录的输出校验结果。"""
    for event in reversed(attempt.events or []):
        if event.get("workflow_instance_id") != workflow.workflow_instance_id:
            continue
        if event.get("event") == OUTPUTS_VALIDATED_EVENT:
            return "VALIDATED"
        if event.get("event") == OUTPUT_VALIDATION_FAILED_EVENT:
            return "FAILED"
    return None


def validate_successful_workflow_outputs(
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> bool:
    """校验调度成功实例的必需输出并记录业务结果。"""
    if not attempt.requested_outputs:
        return True
    validation_state = workflow_output_validation_state(attempt, workflow)
    if validation_state == "VALIDATED":
        return True
    if validation_state == "FAILED":
        return False
    if attempt.submission_state == RESULT_FAILED_STATE:
        return False
    try:
        validate_required_result_files(
            workspace,
            attempt,
            workflow.workflow_instance_id,
        )
    except ValueError as error:
        attempt.submission_state = RESULT_FAILED_STATE
        attempt.error = str(error)
        append_output_validation_event(
            attempt,
            OUTPUT_VALIDATION_FAILED_EVENT,
            workflow.workflow_instance_id,
            error=str(error),
        )
        return False
    append_output_validation_event(
        attempt,
        OUTPUTS_VALIDATED_EVENT,
        workflow.workflow_instance_id,
    )
    if attempt.submission_state not in {"SUBMIT_FAILED", "AUTO_SAVE_FAILED"}:
        attempt.error = None
    return True


def ensure_successful_workflow_outputs(
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> bool:
    """在调用方事务中惰性校验已持久化的调度成功结果。"""
    if workflow.state != "SUCCESS":
        raise RuntimeError(
            f"工作流 {workflow.workflow_instance_id} 当前状态为 {workflow.state}，"
            "不能校验成功结果"
        )
    return validate_successful_workflow_outputs(workspace, attempt, workflow)


def append_output_validation_event(
    attempt: WorkflowAttempt,
    event: str,
    workflow_instance_id: int,
    **details: Any,
) -> None:
    attempt.events = [
        *(attempt.events or []),
        {
            "event": event,
            "timestamp": utc_now().isoformat(),
            "workflow_instance_id": workflow_instance_id,
            **details,
        },
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
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
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
            raise OSError(f"工作流 {workflow_instance_id} 成功但无法读取结果 {name}: {error}") from error
        except BotoCoreError as error:
            raise OSError(f"工作流 {workflow_instance_id} 成功但无法读取结果 {name}: {error}") from error
        finally:
            storage.close()
        return RedirectResponse(url, headers={"Cache-Control": "private, no-store"})

    output_dir = workspace_output_directory(workspace.application, workspace.workspace_key)
    path = local_result_path(
        workflow_instance_id,
        output_dir,
        name,
        filename,
    )
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
