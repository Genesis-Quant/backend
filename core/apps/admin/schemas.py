"""Administrator API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.apps.users.schemas import UserResponse
from core.scheduler.applications.incremental import (
    normalize_incremental_channel,
    normalize_incremental_workers,
)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    is_admin: bool


class AdminUserListResponse(BaseModel):
    items: list[UserResponse]


class AdminWorkflowInstanceSummary(BaseModel):
    total: int
    active: int
    success: int
    failure: int


class AdminUserSummary(BaseModel):
    total: int
    administrators: int


class AdminWorkflow(BaseModel):
    name: str
    code: int
    version: int
    release_state: str
    execution_type: str | None
    updated_at: datetime | None


class AdminTaskGroup(BaseModel):
    id: int
    name: str
    group_size: int
    use_size: int
    status: str
    description: str


class AdminWorker(BaseModel):
    id: int
    host: str
    port: int
    status: str
    cpu_usage: float | None
    memory_usage: float | None
    thread_pool_usage: float | None
    last_heartbeat_at: datetime | None


class AdminProcessInstance(BaseModel):
    id: int
    name: str
    workflow_code: int
    state: str
    worker_group: str
    started_at: datetime | None
    finished_at: datetime | None
    duration: str | None


class AdminIncrementalWorker(BaseModel):
    name: str
    description: str


class AdminSchedulerOverview(BaseModel):
    available: bool
    error: str | None = None
    project_name: str
    project_code: int | None = None
    workflows: list[AdminWorkflow] = Field(default_factory=list)
    task_groups: list[AdminTaskGroup] = Field(default_factory=list)
    worker_groups: list[str] = Field(default_factory=list)
    workers: list[AdminWorker] = Field(default_factory=list)
    recent_instances: list[AdminProcessInstance] = Field(default_factory=list)
    incremental_workers: list[AdminIncrementalWorker] = Field(
        default_factory=list
    )


class AdminOverviewResponse(BaseModel):
    users: AdminUserSummary
    workflow_instances: AdminWorkflowInstanceSummary
    scheduler: AdminSchedulerOverview


class AdminOutputWorkspace(BaseModel):
    application: str
    workspace_key: str
    path: str
    storage: str
    file_count: int
    size_bytes: int
    modified_at: datetime | None
    orphaned: bool
    workflow_workspace_id: int | None = None
    project_id: int | None = None
    project_title: str | None = None


class AdminOutputApplicationSummary(BaseModel):
    application: str
    workspace_count: int
    file_count: int
    total_bytes: int


class AdminOutputStorageResponse(BaseModel):
    available: bool
    error: str | None = None
    mode: str
    root: str
    workspace_count: int
    orphan_workspace_count: int
    file_count: int
    total_bytes: int
    applications: list[AdminOutputApplicationSummary] = Field(default_factory=list)
    workspaces: list[AdminOutputWorkspace] = Field(default_factory=list)


class AdminActionResponse(BaseModel):
    message: str
    result: Any


class IncrementalUpdateRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workers: list[str] | None = None
    channel: str = "console"
    overwrite: bool = False

    @field_validator("workers")
    @classmethod
    def validate_workers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(normalize_incremental_workers(value))

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        return normalize_incremental_channel(value)


class IncrementalUpdateRunResponse(BaseModel):
    message: str
    job_id: str
    workers: list[str]
    channel: str
    overwrite: bool
    workspace_id: int
    workflow_instance_id: int
    project_code: int
    workflow_definition_code: int
    scheduler_submission: Any
