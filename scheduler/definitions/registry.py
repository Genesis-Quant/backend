"""Registration and discovery of Backend-managed workflows."""

from __future__ import annotations

from threading import RLock
from typing import Any

from scheduler.clients import DolphinSchedulerClient
from scheduler.config import DolphinSchedulerSettings
from scheduler.definitions.applications import create_application_workflows
from scheduler.definitions.incremental import (
    create_incremental_update_workflow,
    incremental_task_counts,
)
from scheduler.domain import APPLICATIONS, JobKind
from scheduler.errors import DolphinSchedulerError

_workflow_lock = RLock()


def workflow_names(settings: DolphinSchedulerSettings) -> dict[JobKind, str]:
    return {
        **settings.application_workflow_names,
        "incremental-update": settings.workflow_name,
    }


def managed_workflow_definitions(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    """Return the project and currently registered managed definitions."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    with DolphinSchedulerClient(current_settings) as client:
        project_code = client.project_code(current_settings.project_name)
        if project_code is None:
            return {
                "project_name": current_settings.project_name,
                "project_code": None,
                "workflows": {},
            }
        definitions = {}
        for key, name in workflow_names(current_settings).items():
            definition = client.process_definition(project_code, name)
            if definition:
                definitions[key] = _definition_summary(definition)
        incremental = definitions.get("incremental-update")
        if incremental is not None:
            task_group = next(
                (
                    group
                    for group in client.task_groups(project_code=project_code)
                    if group.get("name")
                    == current_settings.incremental_task_group_name
                ),
                None,
            )
            if task_group is not None:
                incremental["task_group"] = {
                    "id": int(task_group["id"]),
                    "name": task_group["name"],
                    "group_size": int(task_group["groupSize"]),
                    "use_size": int(task_group.get("useSize", 0)),
                }
            incremental.update(incremental_task_counts())
        return {
            "project_name": current_settings.project_name,
            "project_code": project_code,
            "workflows": definitions,
        }


def ensure_all_workflows(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    """Create or update every workflow managed by the Backend."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    with _workflow_lock:
        application_result = create_application_workflows(current_settings)
        incremental_result = create_incremental_update_workflow(current_settings)
    return {
        "project_name": current_settings.project_name,
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


def ensure_workflow_definition(
    workflow: JobKind,
    settings: DolphinSchedulerSettings | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return one managed definition, registering it when missing."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    name = workflow_names(current_settings)[workflow]
    with _workflow_lock:
        found = _find_definition(current_settings, name)
        if found is None:
            if workflow in APPLICATIONS:
                create_application_workflows(current_settings)
            else:
                create_incremental_update_workflow(current_settings)
            found = _find_definition(current_settings, name)
        if found is None:
            raise DolphinSchedulerError(
                f"工作流创建后仍无法通过 API 查询: {name}"
            )
        return found


def _find_definition(
    settings: DolphinSchedulerSettings,
    name: str,
) -> tuple[int, dict[str, Any]] | None:
    with DolphinSchedulerClient(settings) as client:
        project_code = client.project_code(settings.project_name)
        if project_code is None:
            return None
        definition = client.process_definition(project_code, name)
        if not definition:
            return None
        return project_code, definition


def _definition_summary(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        key: definition.get(key)
        for key in (
            "id",
            "code",
            "name",
            "version",
            "releaseState",
            "updateTime",
        )
        if key in definition
    }
