"""Workflow API schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class WorkflowAction(StrEnum):
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RERUN = "rerun"
    RETRY_FAILED = "retry-failed"


class WorkflowTaskInformation(BaseModel):
    task_instance_id: int | None = None
    name: str
    state: str
    host: str | None = None
    duration_seconds: float | None = None


class WorkflowTaskSummary(BaseModel):
    task_code: int | None = None
    task_instance_id: int | None = None
    name: str
    state: str


class WorkflowStartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_parameters: dict[str, str]


class WorkflowInputPayload(WorkflowStartPayload):
    input_json: dict[str, Any]


class WorkflowStatusInformation(BaseModel):
    state: str
    error: str | None


class WorkflowWorkspaceStatus(BaseModel):
    workflow_instance_id: int | None
    state: str
    error: str | None
    events: list[dict[str, Any]]
    updated_at: datetime


class WorkflowTasks(BaseModel):
    state: str
    error: str | None
    tasks: list[WorkflowTaskInformation]


class WorkflowInformation(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    workspace_id: int
    user_id: int
    workflow_instance_id: int
    project_code: int
    workflow_definition_code: int
    workflow_name: str
    state: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    task_count: int
    payload: WorkflowInputPayload | WorkflowStartPayload
    requested_outputs: list[str]
    state_history: list[dict[str, Any]]
    events: list[dict[str, Any]]


class WorkflowAttemptSummary(BaseModel):
    attempt_id: int
    attempt_number: int
    is_current: bool
    workflow_instance_id: int | None
    workflow_definition_code: int | None
    state: str
    tasks: list[WorkflowTaskSummary]
    tasks_error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None


class WorkflowWorkspaceListItem(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    workspace_id: int
    user_id: int
    project_id: int | None
    project_title: str | None
    owner_username: str
    attempt_count: int
    current_attempt: WorkflowAttemptSummary


class WorkflowWorkspaceListResponse(BaseModel):
    items: list[WorkflowWorkspaceListItem]
    total: int
    page: int
    page_size: int


class WorkflowAttemptListResponse(BaseModel):
    items: list[WorkflowAttemptSummary]
    total: int
    page: int
    page_size: int


class WorkflowAttemptInformation(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    workspace_id: int
    project_title: str | None
    attempt_id: int
    attempt_number: int
    workflow_instance_id: int | None
    project_code: int | None
    workflow_definition_code: int | None
    workflow_name: str | None
    state: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    last_synced_at: datetime | None
    attempt_created_at: datetime
    attempt_updated_at: datetime
    task_count: int
    payload: WorkflowInputPayload
    requested_outputs: list[str]
    state_history: list[dict[str, Any]]
    events: list[dict[str, Any]]


class WorkflowActionResponse(BaseModel):
    workflow: WorkflowStatusInformation


class WorkflowDeletedResponse(BaseModel):
    application: Literal["query", "factor", "backtest", "incremental"]
    workspace_id: int
    workflow_instance_id: int
