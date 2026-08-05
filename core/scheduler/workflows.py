"""Registration and discovery of application-managed workflows."""

from __future__ import annotations

from threading import RLock
from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.applications import create_application_workflows
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.domain import ApplicationName
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.incremental import create_incremental_update_workflow

WORKFLOW_LOCK = RLock()


def ensure_all_workflows() -> dict[str, Any]:
    """Create or update every application-managed workflow."""
    with WORKFLOW_LOCK:
        application_result = create_application_workflows()
        incremental_result = create_incremental_update_workflow()
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


def ensure_workflow_definition(
    workflow: ApplicationName,
) -> tuple[int, dict[str, Any]]:
    """Return one managed definition, registering it when missing."""
    name = DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[workflow]
    with WORKFLOW_LOCK:
        found = find_definition(name)
        if found is None:
            create_application_workflows()
            found = find_definition(name)
        if found is None:
            raise DolphinSchedulerError(
                f"工作流创建后仍无法通过 API 查询: {name}"
            )
        return found


def ensure_incremental_workflow_definition() -> tuple[int, dict[str, Any]]:
    """Return the incremental-update definition, registering it when missing."""
    name = DolphinSchedulerSettings.WORKFLOW_NAME
    with WORKFLOW_LOCK:
        found = find_definition(name)
        if found is None:
            create_incremental_update_workflow()
            found = find_definition(name)
        if found is None:
            raise DolphinSchedulerError(
                f"工作流创建后仍无法通过 API 查询: {name}"
            )
        return found


def find_definition(name: str) -> tuple[int, dict[str, Any]] | None:
    with DolphinSchedulerClient() as client:
        project_code = client.project_code(DolphinSchedulerSettings.PROJECT_NAME)
        if project_code is None:
            return None
        definition = client.process_definition(project_code, name)
        if not definition:
            return None
        return project_code, definition
