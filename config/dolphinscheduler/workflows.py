"""Registration and discovery of Backend-managed workflows."""

from __future__ import annotations

from threading import RLock
from typing import Any

from config.dolphinscheduler.applications import create_application_workflows
from config.dolphinscheduler.client import DolphinSchedulerClient
from config.dolphinscheduler.domain import ApplicationName
from config.dolphinscheduler.errors import DolphinSchedulerError
from config.dolphinscheduler.incremental import create_incremental_update_workflow
from config.settings import DolphinSchedulerSettings

WORKFLOW_LOCK = RLock()


def ensure_all_workflows(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    """Create or update every workflow managed by the Backend."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    with WORKFLOW_LOCK:
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
    workflow: ApplicationName,
    settings: DolphinSchedulerSettings | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return one managed definition, registering it when missing."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    name = current_settings.application_workflow_names[workflow]
    with WORKFLOW_LOCK:
        found = find_definition(current_settings, name)
        if found is None:
            create_application_workflows(current_settings)
            found = find_definition(current_settings, name)
        if found is None:
            raise DolphinSchedulerError(
                f"工作流创建后仍无法通过 API 查询: {name}"
            )
        return found


def find_definition(
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
