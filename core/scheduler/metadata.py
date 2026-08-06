"""Workflow metadata loaded after DolphinScheduler definitions are synchronized."""

from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.errors import DolphinSchedulerError

SCHEDULER_PROJECT_CODE: int | None = None
WORKFLOW_DEFINITIONS: dict[str, dict[str, Any]] = {}
WORKFLOW_DEFINITION_DETAILS: dict[int, dict[str, Any]] = {}


def initialize_workflow_metadata() -> None:
    """Load immutable workflow definitions and task topology once."""
    names = [
        *DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES.values(),
        DolphinSchedulerSettings.WORKFLOW_NAME,
    ]
    with DolphinSchedulerClient() as client:
        project_code = client.project_code(DolphinSchedulerSettings.PROJECT_NAME)
        if project_code is None:
            raise DolphinSchedulerError(
                f"DolphinScheduler 项目不存在: {DolphinSchedulerSettings.PROJECT_NAME}"
            )
        definitions: dict[str, dict[str, Any]] = {}
        details: dict[int, dict[str, Any]] = {}
        for name in names:
            definition = client.process_definition(project_code, name)
            if definition is None:
                raise DolphinSchedulerError(f"DolphinScheduler 工作流不存在: {name}")
            definition_code = int(definition["code"])
            definitions[name] = definition
            details[definition_code] = client.process_definition_details(
                project_code,
                definition_code,
            )

    global SCHEDULER_PROJECT_CODE, WORKFLOW_DEFINITIONS, WORKFLOW_DEFINITION_DETAILS
    SCHEDULER_PROJECT_CODE = project_code
    WORKFLOW_DEFINITIONS = definitions
    WORKFLOW_DEFINITION_DETAILS = details


def scheduler_project_code() -> int:
    if SCHEDULER_PROJECT_CODE is None:
        raise DolphinSchedulerError("DolphinScheduler 工作流元数据尚未初始化")
    return SCHEDULER_PROJECT_CODE


def workflow_definition(name: str) -> tuple[int, dict[str, Any]]:
    definition = WORKFLOW_DEFINITIONS.get(name)
    if definition is None:
        raise DolphinSchedulerError(f"DolphinScheduler 工作流元数据不存在: {name}")
    return scheduler_project_code(), definition


def workflow_definitions() -> list[dict[str, Any]]:
    return list(WORKFLOW_DEFINITIONS.values())


def workflow_definition_details(definition_code: int) -> dict[str, Any]:
    details = WORKFLOW_DEFINITION_DETAILS.get(definition_code)
    if details is None:
        raise DolphinSchedulerError(
            f"DolphinScheduler 工作流拓扑不存在: {definition_code}"
        )
    return details
