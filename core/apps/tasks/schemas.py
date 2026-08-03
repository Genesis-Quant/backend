"""Authenticated DolphinScheduler task gateway schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class TaskAction(StrEnum):
    STOP = "stop"
    FORCE_SUCCESS = "force-success"
    PAUSE = "pause"
    RESUME = "resume"
    RERUN = "rerun"
    RETRY_FAILED = "retry-failed"


class WorkflowTaskInformation(BaseModel):
    task_code: int | None = None
    task_id: int | None
    name: str
    task_type: str | None = None
    state: str


class TaskInformation(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    record_id: int
    user_id: int
    task_id: int | None
    task_id_history: list[int]
    process_instance_id: int | None
    process_instance_history: list[int]
    workflow_tasks: list[WorkflowTaskInformation]
    project_code: int | None
    process_definition_code: int | None
    workflow_name: str | None
    process_state: str | None
    state: str
    error: str | None
    host: str | None
    retry_times: int | None
    max_retry_times: int | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    state_history: list[dict[str, Any]]
    events: list[dict[str, Any]]


class TaskStatusResponse(TaskInformation):
    requested_task_id: int


class TaskListItem(TaskInformation):
    owner_username: str
    payload: dict[str, Any]
    requested_outputs: list[str]


class TaskListResponse(BaseModel):
    items: list[TaskListItem]
    total: int
    page: int
    page_size: int


class TaskLogResponse(BaseModel):
    task_id: int
    state: str
    skip_line_num: int
    returned_lines: int
    next_line_num: int
    has_more: bool
    message: str


class TaskActionResponse(BaseModel):
    action: TaskAction
    scheduler_submission: Any
    task: TaskStatusResponse


class TaskDeletedResponse(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    record_id: int
    task_id: int
