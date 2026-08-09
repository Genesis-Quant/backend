"""Administrator operations backed by Arena and DolphinScheduler."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NotRequired, TypedDict

from botocore.exceptions import BotoCoreError, ClientError
from runtime.utils.storage import ObjectStorage, ObjectStorageConfigurationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import ArenaSettings, DolphinSchedulerSettings
from core.apps.users.models import User
from core.apps.workflows.artifacts import (
    WORKSPACE_APPLICATIONS,
    uses_cloud_output,
    validate_workspace_key,
    workspace_directory,
)
from core.apps.workflows.models import WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    WorkflowGatewayService,
    current_workflow_instance,
    require_current_workflow_attempt,
    workspace_project_references,
)
from core.scheduler.applications import create_application_workflows
from core.scheduler.applications.incremental import (
    create_incremental_update_workflow,
    incremental_worker_options,
)
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.domain import FAILURE_STATES, TERMINAL_STATES
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.metadata import (
    initialize_workflow_metadata,
    scheduler_project_code,
    workflow_definitions,
)
from core.scheduler.workflows import WORKFLOW_LOCK
from core.utils.results import delete_cloud_result_objects


class WorkspaceUsage(TypedDict):
    application: str
    workspace_key: str
    path: str
    file_count: int
    size_bytes: int
    modified_at: datetime | None
    storage_modes: NotRequired[set[str]]
    storage: NotRequired[str]
    orphaned: NotRequired[bool]
    workflow_workspace_id: NotRequired[int | None]
    project_id: NotRequired[int | None]
    project_title: NotRequired[str | None]


class ApplicationUsage(TypedDict):
    application: str
    workspace_count: int
    file_count: int
    total_bytes: int


class AdminService:
    def overview(self, session: Session) -> dict[str, Any]:
        return {
            "users": self.user_summary(session),
            "workflow_instances": self.workflow_summary(session),
            "scheduler": self.scheduler_overview(),
        }

    @staticmethod
    def output_storage(session: Session) -> dict[str, object]:
        mode = "cloud" if ArenaSettings.SHARED_CLOUD else "local"
        try:
            root, workspaces = output_workspaces()
        except (BotoCoreError, ClientError, ObjectStorageConfigurationError, OSError, ValueError) as error:
            return empty_output_storage(mode, output_storage_root(mode), str(error))
        enriched = enrich_workspace_ownership(session, workspaces)
        applications: dict[str, ApplicationUsage] = {}
        for workspace in enriched:
            application = workspace["application"]
            summary = applications.setdefault(
                application,
                {
                    "application": application,
                    "workspace_count": 0,
                    "file_count": 0,
                    "total_bytes": 0,
                },
            )
            summary["workspace_count"] += 1
            summary["file_count"] += workspace["file_count"]
            summary["total_bytes"] += workspace["size_bytes"]
        return {
            "available": True,
            "error": None,
            "mode": mode,
            "root": root,
            "workspace_count": len(enriched),
            "orphan_workspace_count": sum(bool(item["orphaned"]) for item in enriched),
            "file_count": sum(item["file_count"] for item in enriched),
            "total_bytes": sum(item["size_bytes"] for item in enriched),
            "applications": sorted(
                applications.values(),
                key=lambda item: (-item["total_bytes"], item["application"]),
            ),
            "workspaces": sorted(
                enriched,
                key=lambda item: (
                    not bool(item["orphaned"]),
                    -item["size_bytes"],
                    item["path"],
                ),
            ),
        }

    @staticmethod
    def delete_orphan_workspace(
        session: Session,
        application: str,
        workspace_key: str,
    ) -> dict[str, str]:
        key = validate_workspace_key(workspace_key)
        if application not in WORKSPACE_APPLICATIONS:
            raise ValueError(f"无效的 workspace application: {application}")
        owner = session.scalar(
            select(WorkflowWorkspace.id).where(
                WorkflowWorkspace.application == application,
                WorkflowWorkspace.workspace_key == key,
            )
        )
        if owner is not None:
            raise RuntimeError(f"workspace 仍归属于工作流工作空间 #{owner}，不能删除")
        if ArenaSettings.SHARED_CLOUD:
            delete_cloud_result_objects(application, key)
        directory = workspace_directory(application, key)
        if directory.exists():
            shutil.rmtree(directory)
        return {"application": application, "workspace_key": key}

    @staticmethod
    def users(session: Session) -> list[User]:
        return list(session.scalars(select(User).order_by(User.created_at, User.id)))

    @staticmethod
    def update_user(session: Session, actor: User, user_id: int, is_admin: bool) -> User:
        user = session.get(User, user_id)
        if user is None:
            raise FileNotFoundError(f"用户不存在: {user_id}")
        if user.id == actor.id and not is_admin:
            raise RuntimeError("不能取消自己的管理员权限")
        user.is_admin = is_admin
        session.commit()
        session.refresh(user)
        return user

    @staticmethod
    def ensure_workflows() -> dict[str, Any]:
        with WORKFLOW_LOCK:
            application_result = create_application_workflows()
            incremental_result = create_incremental_update_workflow()
            initialize_workflow_metadata()
        return {
            "project_name": DolphinSchedulerSettings.PROJECT_NAME,
            "workflows": {
                **application_result["workflows"],
                "incremental-update": {
                    "name": incremental_result["name"],
                    "code": incremental_result["workflow_code"],
                    "worker_task_count": incremental_result["worker_task_count"],
                    "control_task_count": incremental_result["control_task_count"],
                    "task_count": incremental_result["task_count"],
                    "task_group": incremental_result["task_group"],
                },
            },
        }

    @staticmethod
    def run_incremental_update(
        session: Session,
        user_id: int,
        workers: Sequence[str] | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        run, submission = WorkflowGatewayService().submit_incremental(
            session,
            user_id,
            workers,
            channel,
        )
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        attempt = require_current_workflow_attempt(session, run.id)
        start_parameters = attempt.start_parameters
        return {
            "message": "增量更新工作流已提交",
            "job_id": start_parameters["job_id"],
            "workers": start_parameters["workers"].split(","),
            "channel": start_parameters["channel"],
            "workspace_id": run.id,
            "workflow_instance_id": workflow.workflow_instance_id,
            "project_code": int(attempt.project_code or 0),
            "workflow_definition_code": int(attempt.workflow_definition_code or 0),
            "scheduler_submission": submission,
        }

    @staticmethod
    def user_summary(session: Session) -> dict[str, int]:
        total, administrators = session.execute(
            select(
                func.count(User.id),
                func.count(User.id).filter(User.is_admin.is_(True)),
            )
        ).one()
        return {
            "total": int(total),
            "administrators": int(administrators),
        }

    @staticmethod
    def workflow_summary(session: Session) -> dict[str, int]:
        total, active, success, failure = session.execute(
            select(
                func.count(WorkflowInstance.workflow_instance_id),
                func.count(WorkflowInstance.workflow_instance_id).filter(
                    WorkflowInstance.state.not_in(TERMINAL_STATES)
                ),
                func.count(WorkflowInstance.workflow_instance_id).filter(
                    WorkflowInstance.state.in_(("SUCCESS", "FORCED_SUCCESS"))
                ),
                func.count(WorkflowInstance.workflow_instance_id).filter(
                    WorkflowInstance.state.in_(FAILURE_STATES)
                ),
            )
        ).one()
        return {
            "total": int(total),
            "active": int(active),
            "success": int(success),
            "failure": int(failure),
        }

    @staticmethod
    def scheduler_overview() -> dict[str, Any]:
        base: dict[str, Any] = {
            "available": False,
            "project_name": DolphinSchedulerSettings.PROJECT_NAME,
            "workflows": [],
            "task_groups": [],
            "worker_groups": [],
            "workers": [],
            "recent_instances": [],
            "incremental_workers": incremental_worker_options(),
        }
        try:
            with DolphinSchedulerClient() as client:
                project_code = scheduler_project_code()
                base.update({
                    "available": True,
                    "project_code": project_code,
                    "workflows": [workflow_definition_information(item) for item in workflow_definitions()],
                    "task_groups": [task_group_information(item) for item in client.task_groups(project_code=project_code)],
                    "worker_groups": client.worker_groups(),
                    "workers": [worker_information(item) for item in client.workers()],
                    "recent_instances": [process_instance_information(item) for item in client.process_instances(project_code=project_code, page_size=20)],
                })
        except DolphinSchedulerError as error:
            base["error"] = str(error)
        return base


def workflow_definition_information(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(definition.get("name") or ""),
        "code": int(definition.get("code") or 0),
        "version": int(definition.get("version") or 0),
        "release_state": str(definition.get("releaseState") or "UNKNOWN"),
        "execution_type": optional_string(definition.get("executionType")),
        "updated_at": definition.get("updateTime"),
    }


def task_group_information(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(group.get("id") or 0),
        "name": str(group.get("name") or ""),
        "group_size": int(group.get("groupSize") or 0),
        "use_size": int(group.get("useSize") or 0),
        "status": str(group.get("status") or "UNKNOWN"),
        "description": str(group.get("description") or ""),
    }


def worker_information(worker: dict[str, Any]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    try:
        resources = json.loads(worker.get("resInfo") or "{}")
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "id": int(worker.get("id") or 0),
        "host": str(worker.get("host") or ""),
        "port": int(worker.get("port") or 0),
        "status": str(resources.get("serverStatus") or "UNKNOWN"),
        "cpu_usage": optional_float(resources.get("cpuUsage")),
        "memory_usage": optional_float(resources.get("memoryUsage")),
        "thread_pool_usage": optional_float(resources.get("threadPoolUsage")),
        "last_heartbeat_at": worker.get("lastHeartbeatTime"),
    }


def process_instance_information(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(instance.get("id") or 0),
        "name": str(instance.get("name") or ""),
        "workflow_code": int(instance.get("processDefinitionCode") or 0),
        "state": str(instance.get("state") or "UNKNOWN"),
        "worker_group": str(instance.get("workerGroup") or ""),
        "started_at": instance.get("startTime"),
        "finished_at": instance.get("endTime"),
        "duration": optional_string(instance.get("duration")),
    }


def optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def output_workspaces() -> tuple[str, list[WorkspaceUsage]]:
    local_root, local = local_output_workspaces()
    if not ArenaSettings.SHARED_CLOUD:
        return local_root, finalize_workspace_storage(local)
    cloud_root, cloud = cloud_output_workspaces()
    return cloud_root, finalize_workspace_storage(merge_workspace_usage(local, cloud))


def local_output_workspaces() -> tuple[str, list[WorkspaceUsage]]:
    root = ArenaSettings.SHARED_DIR.resolve()
    if not root.is_dir():
        raise OSError(f"共享输出目录不存在: {root}")
    workspaces: list[WorkspaceUsage] = []
    for application in sorted(WORKSPACE_APPLICATIONS):
        application_directory = root / application
        if application_directory.is_symlink() or not application_directory.is_dir():
            continue
        for directory in application_directory.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                workspace_key = validate_workspace_key(directory.name)
            except ValueError:
                continue
            output_directory = directory / "output"
            file_count, size_bytes, modified_at = directory_output_usage(
                output_directory
            )
            workspaces.append({
                "application": application,
                "workspace_key": workspace_key,
                "path": f"{application}/{workspace_key}/output",
                "file_count": file_count,
                "size_bytes": size_bytes,
                "modified_at": modified_at,
                "storage_modes": {"local"} if output_directory.is_dir() else set(),
            })
    return str(root), workspaces


def directory_output_usage(
    output_directory: Path,
) -> tuple[int, int, datetime | None]:
    if output_directory.is_symlink() or not output_directory.is_dir():
        return 0, 0, None
    file_count = 0
    size_bytes = 0
    modified_at: datetime | None = None
    for directory, directory_names, filenames in os.walk(
        output_directory,
        followlinks=False,
    ):
        directory_names[:] = [
            name
            for name in directory_names
            if not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            item_modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
            file_count += 1
            size_bytes += stat.st_size
            modified_at = latest_datetime(modified_at, item_modified_at)
    return file_count, size_bytes, modified_at


def cloud_output_workspaces() -> tuple[str, list[WorkspaceUsage]]:
    storage = ObjectStorage.from_env()
    prefix = f"{storage.root_folder}/" if storage.root_folder else ""
    workspaces: dict[tuple[str, str], WorkspaceUsage] = {}
    try:
        paginator = storage.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=storage.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if not key or key.endswith("/") or not key.startswith(prefix):
                    continue
                relative_key = key[len(prefix):]
                parts = PurePosixPath(relative_key).parts
                if (
                    len(parts) < 4
                    or parts[0] not in WORKSPACE_APPLICATIONS
                    or parts[2] != "output"
                ):
                    continue
                try:
                    workspace_key = validate_workspace_key(parts[1])
                except ValueError:
                    continue
                modified_at = item.get("LastModified")
                if not isinstance(modified_at, datetime):
                    raise ValueError(f"对象缺少最后修改时间: {relative_key}")
                identity = (parts[0], workspace_key)
                workspace = workspaces.setdefault(identity, {
                    "application": parts[0],
                    "workspace_key": workspace_key,
                    "path": f"{parts[0]}/{workspace_key}/output",
                    "file_count": 0,
                    "size_bytes": 0,
                    "modified_at": None,
                    "storage_modes": {"cloud"},
                })
                workspace["file_count"] += 1
                workspace["size_bytes"] += int(item.get("Size") or 0)
                workspace["modified_at"] = latest_datetime(
                    workspace["modified_at"],
                    modified_at,
                )
        return output_storage_root("cloud", storage), list(workspaces.values())
    finally:
        storage.close()


def merge_workspace_usage(
    *collections: list[WorkspaceUsage],
) -> list[WorkspaceUsage]:
    merged: dict[tuple[str, str], WorkspaceUsage] = {}
    for collection in collections:
        for item in collection:
            identity = (str(item["application"]), str(item["workspace_key"]))
            current = merged.get(identity)
            if current is None:
                copied = item.copy()
                copied["storage_modes"] = set(item.get("storage_modes", set()))
                merged[identity] = copied
                continue
            current["file_count"] += item["file_count"]
            current["size_bytes"] += item["size_bytes"]
            current["modified_at"] = latest_datetime(
                current["modified_at"],
                item["modified_at"],
            )
            current["storage_modes"] = set(current.get("storage_modes", set())) | set(
                item.get("storage_modes", set())
            )
    return list(merged.values())


def finalize_workspace_storage(
    workspaces: list[WorkspaceUsage],
) -> list[WorkspaceUsage]:
    for workspace in workspaces:
        modes = workspace.pop("storage_modes", set())
        if not modes:
            modes.add(
                "cloud"
                if uses_cloud_output(str(workspace["application"]))
                else "local"
            )
        workspace["storage"] = next(iter(modes)) if len(modes) == 1 else "mixed"
    return workspaces


def enrich_workspace_ownership(
    session: Session,
    workspaces: list[WorkspaceUsage],
) -> list[WorkspaceUsage]:
    keys = [str(item["workspace_key"]) for item in workspaces]
    workflow_workspaces = (
        list(session.scalars(select(WorkflowWorkspace).where(WorkflowWorkspace.workspace_key.in_(keys))))
        if keys
        else []
    )
    workspace_by_identity = {
        (workspace.application, workspace.workspace_key): workspace
        for workspace in workflow_workspaces
    }
    project_references = workspace_project_references(session, workflow_workspaces)
    enriched: list[WorkspaceUsage] = []
    for workspace in workspaces:
        identity = (
            str(workspace["application"]),
            str(workspace["workspace_key"]),
        )
        workflow_workspace = workspace_by_identity.get(identity)
        project_reference = project_references.get(workflow_workspace.id) if workflow_workspace is not None else None
        item = workspace.copy()
        item["orphaned"] = workflow_workspace is None
        item["workflow_workspace_id"] = workflow_workspace.id if workflow_workspace is not None else None
        item["project_id"] = project_reference[0] if project_reference is not None else None
        item["project_title"] = project_reference[1] if project_reference is not None else None
        enriched.append(item)
    return enriched


def latest_datetime(
    left: object,
    right: object,
) -> datetime | None:
    values = [value for value in (left, right) if isinstance(value, datetime)]
    return max(values) if values else None


def output_storage_root(mode: str, storage: ObjectStorage | None = None) -> str:
    if mode == "local":
        return str(ArenaSettings.SHARED_DIR.resolve())
    if storage is not None:
        suffix = f"/{storage.root_folder}" if storage.root_folder else ""
        return f"s3://{storage.bucket}{suffix}"
    return "对象存储"


def empty_output_storage(mode: str, root: str, error: str) -> dict[str, object]:
    return {
        "available": False,
        "error": error,
        "mode": mode,
        "root": root,
        "workspace_count": 0,
        "orphan_workspace_count": 0,
        "file_count": 0,
        "total_bytes": 0,
        "applications": [],
        "workspaces": [],
    }
