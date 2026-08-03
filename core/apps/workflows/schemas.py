"""Workflow API schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class WorkflowAction(StrEnum):
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RERUN = "rerun"
    RETRY_FAILED = "retry-failed"


class WorkflowTaskInformation(BaseModel):
    task_code: int | None = None
    task_instance_id: int | None = None
    name: str
    task_type: str | None = None
    state: str
    host: str | None = None
    retry_times: int | None = None
    max_retry_times: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class WorkflowInformation(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    record_id: int
    user_id: int
    workflow_instance_id: int
    project_code: int
    workflow_definition_code: int
    workflow_name: str
    state: str
    tasks: list[WorkflowTaskInformation]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    state_history: list[dict[str, Any]]
    events: list[dict[str, Any]]


class WorkflowListItem(WorkflowInformation):
    owner_username: str
    payload: dict[str, Any]
    requested_outputs: list[str]
    tasks_error: str | None = None


class WorkflowListResponse(BaseModel):
    items: list[WorkflowListItem]
    total: int
    page: int
    page_size: int


class WorkflowActionResponse(BaseModel):
    action: WorkflowAction
    scheduler_submission: Any
    synchronization_error: str | None = None
    workflow: WorkflowInformation


class WorkflowDeletedResponse(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    record_id: int
    workflow_instance_id: int
