"""Result-file access keyed by DolphinScheduler workflow instance ID."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from stat import S_ISREG
from tempfile import SpooledTemporaryFile
from threading import Lock
from typing import Any, BinaryIO, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Response
from fastapi.responses import FileResponse, RedirectResponse
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, Field
from runtime.utils.storage import (
    MAX_RESULT_MANIFEST_SIZE,
    PARQUET_CONTENT_TYPE,
    RESULT_MANIFEST_FILENAME,
    RESULT_MANIFEST_VERSION,
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

LOGGER = logging.getLogger(__name__)

RESULT_FAILED_STATE = "RESULT_FAILED"
OUTPUTS_VALIDATED_EVENT = "WORKFLOW_OUTPUTS_VALIDATED"
OUTPUT_VALIDATION_FAILED_EVENT = "WORKFLOW_OUTPUT_VALIDATION_FAILED"
HASH_CHUNK_SIZE = 1024 * 1024
METADATA_SPOOL_MAX_SIZE = 64 * 1024 * 1024
METADATA_CACHE_SIZE = 256


class ResultColumn(BaseModel):
    """One top-level Parquet column in physical file order."""

    name: str
    type: str
    nullable: bool


class ParquetResultMetadata(BaseModel):
    row_count: int = Field(ge=0)
    columns: list[ResultColumn]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResultFile[Name: str](ParquetResultMetadata):
    name: Name
    filename: str
    size: int
    modified_at: datetime


class ResultManifestEntry(ParquetResultMetadata):
    """Persisted metadata bound to one immutable output-file snapshot."""

    filename: str
    size: int = Field(ge=0)
    modified_at: datetime
    snapshot_token: str | None = None


class ResultManifest(BaseModel):
    """Small sidecar used to list outputs without rereading Parquet bodies."""

    version: Literal[1] = RESULT_MANIFEST_VERSION
    files: dict[str, ResultManifestEntry]


@dataclass(frozen=True)
class ResultSnapshot:
    """Storage identity used to invalidate cached content metadata."""

    size: int
    modified_at: datetime
    cache_token: str | None = None


_RESULT_METADATA_CACHE: OrderedDict[str, ParquetResultMetadata] = OrderedDict()
_RESULT_METADATA_CACHE_LOCK = Lock()


class WorkflowOutputValidationError(ValueError):
    """表示工作流输出确定性地缺失、为空或不完整。"""


class ResultNotRequestedError(FileNotFoundError):
    """The requested output belongs to the API but not to this attempt."""

    api_code = "RESULT_NOT_REQUESTED"


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
    *,
    legacy_optional_outputs: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    workspace, attempt = owned_result_workspace(session, user_id, workflow_instance_id, application)
    if uses_cloud_output(workspace.application):
        storage, output_key = cloud_storage(workspace.application, workspace.workspace_key)
        try:
            manifest = read_cloud_result_manifest(storage, output_key)
            files: list[dict[str, Any]] = []
            for name in attempt.requested_outputs:
                try:
                    file = cloud_result_file(
                        workflow_instance_id,
                        storage,
                        output_key,
                        name,
                        result_filename(workflow_instance_id, name, output_files),
                        manifest_entry=(manifest.files.get(name) if manifest else None),
                    )
                    files.append(file)
                except ClientError as error:
                    if name in legacy_optional_outputs and is_missing_object(error):
                        continue
                    raise
            if manifest is None or any(not file["_manifest_hit"] for file in files):
                try:
                    write_cloud_result_manifest(
                        storage,
                        output_key,
                        result_manifest_from_files(files),
                    )
                except (BotoCoreError, ClientError, OSError) as error:
                    LOGGER.warning(
                        "Unable to backfill result manifest for workflow %s: %s",
                        workflow_instance_id,
                        error,
                    )
        except (BotoCoreError, ClientError) as error:
            raise OSError(
                f"工作流 {workflow_instance_id} 无法读取对象存储结果: {error}"
            ) from error
        finally:
            storage.close()
    else:
        output_dir = workspace_output_directory(workspace.application, workspace.workspace_key)
        manifest = read_local_result_manifest(output_dir)
        files = []
        for name in attempt.requested_outputs:
            try:
                file = local_result_file(
                    workflow_instance_id,
                    output_dir,
                    name,
                    result_filename(workflow_instance_id, name, output_files),
                    manifest_entry=(manifest.files.get(name) if manifest else None),
                )
                files.append(file)
            except FileNotFoundError:
                if name in legacy_optional_outputs:
                    continue
                raise
        if manifest is None or any(not file["_manifest_hit"] for file in files):
            try:
                write_local_result_manifest(
                    output_dir,
                    result_manifest_from_files(files),
                )
            except OSError as error:
                LOGGER.warning(
                    "Unable to backfill result manifest for workflow %s: %s",
                    workflow_instance_id,
                    error,
                )
    return [public_result_file(file) for file in files]


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
        raise ResultNotRequestedError(f"工作流未请求结果: {name}")
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
    try:
        path = local_result_path(
            workflow_instance_id,
            output_dir,
            name,
            filename,
        )
    except FileNotFoundError as error:
        raise OSError(
            f"工作流 {workflow_instance_id} 成功但缺少已请求结果: {name}"
        ) from error
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
    *,
    manifest_entry: ResultManifestEntry | None = None,
) -> dict[str, Any]:
    path = local_result_path(
        workflow_instance_id,
        output_dir,
        name,
        filename,
    )
    initial_stat = path.stat()
    initial_snapshot = local_result_snapshot(initial_stat)
    if manifest_entry_matches(
        manifest_entry,
        filename,
        initial_snapshot,
    ):
        return result_file_from_manifest(
            name,
            manifest_entry,
            initial_snapshot,
            manifest_hit=True,
        )

    for attempt in range(2):
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not S_ISREG(before.st_mode):
                raise OSError(
                    f"工作流 {workflow_instance_id} 的结果不是普通文件: {name}"
                )
            snapshot = local_result_snapshot(before)
            cache_key = result_metadata_cache_key("local", str(path), snapshot)
            metadata = cached_result_metadata(cache_key)
            try:
                if metadata is None:
                    metadata = parquet_result_metadata(
                        source,
                        workflow_instance_id,
                        name,
                    )
            except OSError:
                after = os.fstat(source.fileno())
                if attempt == 0 and not same_local_snapshot(before, after):
                    continue
                raise
            after = os.fstat(source.fileno())
        if same_local_snapshot(before, after):
            cache_result_metadata(cache_key, metadata)
            return {
                "name": name,
                "filename": filename,
                "size": snapshot.size,
                "modified_at": snapshot.modified_at,
                "_snapshot_token": snapshot.cache_token,
                "_manifest_hit": False,
                **metadata.model_dump(),
            }
    raise OSError(f"工作流结果 {name} 在读取元数据时持续变化")


def local_result_snapshot(file_stat: os.stat_result) -> ResultSnapshot:
    return ResultSnapshot(
        size=file_stat.st_size,
        modified_at=datetime.fromtimestamp(file_stat.st_mtime, UTC),
        cache_token=(
            f"local:{file_stat.st_mtime_ns}:"
            f"{getattr(file_stat, 'st_ino', 0)}"
        ),
    )


def same_local_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        getattr(left, "st_ino", 0),
    ) == (
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        getattr(right, "st_ino", 0),
    )


def read_local_result_manifest(output_dir: Path) -> ResultManifest | None:
    """Read the bounded local sidecar; invalid legacy sidecars are ignored."""
    path = output_dir / RESULT_MANIFEST_FILENAME
    try:
        if path.is_symlink():
            return None
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size <= 0 or size > MAX_RESULT_MANIFEST_SIZE:
        return None
    try:
        return ResultManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def manifest_entry_matches(
    entry: ResultManifestEntry | None,
    filename: str,
    snapshot: ResultSnapshot,
) -> bool:
    """Require the sidecar to describe the current storage snapshot exactly."""
    if entry is None:
        return False
    if (
        entry.filename != filename
        or entry.size != snapshot.size
        or entry.modified_at != snapshot.modified_at
    ):
        return False
    return entry.snapshot_token is None or entry.snapshot_token == snapshot.cache_token


def result_file_from_manifest(
    name: str,
    entry: ResultManifestEntry,
    snapshot: ResultSnapshot,
    *,
    manifest_hit: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "filename": entry.filename,
        "size": snapshot.size,
        "modified_at": snapshot.modified_at,
        "_snapshot_token": snapshot.cache_token,
        "_manifest_hit": manifest_hit,
        **ParquetResultMetadata(
            row_count=entry.row_count,
            columns=entry.columns,
            sha256=entry.sha256,
        ).model_dump(),
    }


def public_result_file(file: dict[str, Any]) -> dict[str, Any]:
    """Remove storage-only fields before returning the API/MCP contract."""
    return {
        key: value
        for key, value in file.items()
        if key not in {"_snapshot_token", "_manifest_hit"}
    }


def result_manifest_from_files(files: list[dict[str, Any]]) -> ResultManifest:
    return ResultManifest(
        files={
            str(file["name"]): ResultManifestEntry(
                filename=str(file["filename"]),
                size=int(file["size"]),
                modified_at=file["modified_at"],
                snapshot_token=file.get("_snapshot_token"),
                row_count=int(file["row_count"]),
                columns=file["columns"],
                sha256=str(file["sha256"]),
            )
            for file in files
        },
    )


def result_manifest_bytes(manifest: ResultManifest) -> bytes:
    return manifest.model_dump_json(exclude_none=False).encode("utf-8")


def write_local_result_manifest(
    output_dir: Path,
    manifest: ResultManifest,
) -> None:
    """Atomically backfill a local sidecar after reading legacy outputs."""
    path = output_dir / RESULT_MANIFEST_FILENAME
    temporary = output_dir / f"{RESULT_MANIFEST_FILENAME}.tmp"
    if path.is_symlink() or temporary.is_symlink():
        raise OSError("结果清单路径不能是符号链接")
    data = result_manifest_bytes(manifest)
    if len(data) > MAX_RESULT_MANIFEST_SIZE:
        raise OSError("结果清单超过大小限制")
    temporary.write_bytes(data)
    temporary.replace(path)


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
    workflow_instance_id: int,
    storage: ObjectStorage,
    output_key: str,
    name: str,
    filename: str,
    *,
    manifest_entry: ResultManifestEntry | None = None,
) -> dict[str, Any]:
    key = f"{output_key}/{filename}"
    for attempt in range(2):
        head = storage.client.head_object(Bucket=storage.bucket, Key=key)
        etag = normalized_etag(head.get("ETag"))
        version_id = normalized_version_id(head.get("VersionId"))
        snapshot = ResultSnapshot(
            size=int(head["ContentLength"]),
            modified_at=head["LastModified"],
            cache_token=f"cloud:{etag or ''}:{version_id or ''}",
        )
        if manifest_entry_matches(manifest_entry, filename, snapshot):
            return result_file_from_manifest(
                name,
                manifest_entry,
                snapshot,
                manifest_hit=True,
            )
        identity = (
            f"{storage_endpoint_identity(storage)}:{storage.bucket}:{key}"
        )
        cache_key = result_metadata_cache_key("cloud", identity, snapshot)
        metadata = cached_result_metadata(cache_key)
        if metadata is not None:
            break
        request: dict[str, Any] = {"Bucket": storage.bucket, "Key": key}
        if version_id is not None:
            request["VersionId"] = version_id
        elif head.get("ETag"):
            request["IfMatch"] = head["ETag"]
        try:
            response = storage.client.get_object(**request)
        except ClientError as error:
            if attempt == 0 and is_object_snapshot_changed(error):
                continue
            raise
        body = response["Body"]
        try:
            with SpooledTemporaryFile(
                max_size=METADATA_SPOOL_MAX_SIZE,
                mode="w+b",
            ) as source:
                digest = hashlib.sha256()
                downloaded = 0
                while chunk := body.read(HASH_CHUNK_SIZE):
                    digest.update(chunk)
                    source.write(chunk)
                    downloaded += len(chunk)
                if downloaded != snapshot.size:
                    raise OSError(
                        f"工作流结果 {name} 下载不完整: "
                        f"预期 {snapshot.size} bytes，实际 {downloaded} bytes"
                    )
                source.seek(0)
                metadata = parquet_result_metadata(
                    source,
                    workflow_instance_id,
                    name,
                    sha256=digest.hexdigest(),
                )
        finally:
            body.close()
        cache_result_metadata(cache_key, metadata)
        break
    else:  # pragma: no cover - the loop always returns or raises
        raise OSError(f"工作流结果 {name} 的对象版本持续变化")
    result = {
        "name": name,
        "filename": filename,
        "size": snapshot.size,
        "modified_at": snapshot.modified_at,
        "_snapshot_token": snapshot.cache_token,
        "_manifest_hit": False,
        **metadata.model_dump(),
    }
    return result


def read_cloud_result_manifest(
    storage: ObjectStorage,
    output_key: str,
) -> ResultManifest | None:
    """Download only the bounded JSON sidecar, never an output body."""
    key = f"{output_key}/{RESULT_MANIFEST_FILENAME}"
    try:
        response = storage.client.get_object(
            Bucket=storage.bucket,
            Key=key,
            Range=f"bytes=0-{MAX_RESULT_MANIFEST_SIZE}",
        )
    except ClientError as error:
        if is_missing_object(error):
            return None
        raise
    body = response["Body"]
    try:
        data = body.read(MAX_RESULT_MANIFEST_SIZE + 1)
    finally:
        body.close()
    if not data or len(data) > MAX_RESULT_MANIFEST_SIZE:
        return None
    try:
        return ResultManifest.model_validate_json(data)
    except (ValueError, json.JSONDecodeError):
        return None


def write_cloud_result_manifest(
    storage: ObjectStorage,
    output_key: str,
    manifest: ResultManifest,
) -> None:
    """Backfill a cloud sidecar after reading legacy outputs once."""
    data = result_manifest_bytes(manifest)
    if len(data) > MAX_RESULT_MANIFEST_SIZE:
        raise OSError("结果清单超过大小限制")
    storage.client.put_object(
        Bucket=storage.bucket,
        Key=f"{output_key}/{RESULT_MANIFEST_FILENAME}",
        Body=data,
        ContentType="application/json",
    )


def parquet_result_metadata(
    source: BinaryIO,
    workflow_instance_id: int,
    name: str,
    *,
    sha256: str | None = None,
) -> ParquetResultMetadata:
    """Read exact top-level schema/row count and a full-file SHA-256."""
    if sha256 is None:
        digest = hashlib.sha256()
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
        sha256 = digest.hexdigest()
        source.seek(0)
    try:
        parquet = pq.ParquetFile(source)
        schema = parquet.schema_arrow
        return ParquetResultMetadata(
            row_count=parquet.metadata.num_rows,
            columns=[
                ResultColumn(
                    name=field.name,
                    type=str(field.type),
                    nullable=field.nullable,
                )
                for field in schema
            ],
            sha256=sha256,
        )
    except (pa.ArrowException, OSError, ValueError) as error:
        instance = (
            f"工作流 {workflow_instance_id} "
            if workflow_instance_id > 0
            else ""
        )
        raise OSError(
            f"{instance}无法读取结果 {name} 的 Parquet 元数据: {error}"
        ) from error


def result_metadata_cache_key(
    storage_kind: str,
    identity: str,
    snapshot: ResultSnapshot,
) -> str:
    return ":".join((
        storage_kind,
        identity,
        str(snapshot.size),
        snapshot.modified_at.isoformat(),
        snapshot.cache_token or "",
    ))


def cached_result_metadata(key: str) -> ParquetResultMetadata | None:
    with _RESULT_METADATA_CACHE_LOCK:
        metadata = _RESULT_METADATA_CACHE.get(key)
        if metadata is not None:
            _RESULT_METADATA_CACHE.move_to_end(key)
        return metadata


def cache_result_metadata(key: str, metadata: ParquetResultMetadata) -> None:
    with _RESULT_METADATA_CACHE_LOCK:
        _RESULT_METADATA_CACHE[key] = metadata
        _RESULT_METADATA_CACHE.move_to_end(key)
        while len(_RESULT_METADATA_CACHE) > METADATA_CACHE_SIZE:
            _RESULT_METADATA_CACHE.popitem(last=False)


def clear_result_metadata_cache() -> None:
    """Clear the bounded process-local cache (also useful for isolated tests)."""
    with _RESULT_METADATA_CACHE_LOCK:
        _RESULT_METADATA_CACHE.clear()


def normalized_etag(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().strip('"')
    return normalized or None


def normalized_version_id(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized if normalized and normalized != "null" else None


def storage_endpoint_identity(storage: ObjectStorage) -> str:
    metadata = getattr(storage.client, "meta", None)
    return str(getattr(metadata, "endpoint_url", ""))


def is_object_snapshot_changed(error: ClientError) -> bool:
    response = error.response
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 412 or code in {"412", "PreconditionFailed"}


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
