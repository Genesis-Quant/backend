"""DolphinScheduler task APIs."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class TaskAction(StrEnum):
    FORCE_SUCCESS = "force-success"


class TaskLogScope(StrEnum):
    FULL = "full"
    WORKER = "worker"


class TaskLogResponse(BaseModel):
    workflow_instance_id: int
    task_instance_id: int
    state: str
    scope: TaskLogScope = TaskLogScope.FULL
    skip_line_num: int
    returned_lines: int
    next_line_num: int
    has_more: bool
    message: str
    next_cursor: str | None = None


class TaskActionResponse(BaseModel):
    action: TaskAction
    scheduler_submission: Any
    workflow_instance_id: int
    task_instance_id: int
    task: dict[str, Any]
