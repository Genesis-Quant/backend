"""Administrator API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.apps.users.schemas import UserResponse


class AdminUserUpdate(BaseModel):
    is_admin: bool


class AdminUserListResponse(BaseModel):
    items: list[UserResponse]


class AdminTaskSummary(BaseModel):
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


class AdminOverviewResponse(BaseModel):
    users: AdminUserSummary
    tasks: AdminTaskSummary
    scheduler: AdminSchedulerOverview


class AdminActionResponse(BaseModel):
    message: str
    result: Any


class IncrementalUpdateRunResponse(BaseModel):
    message: str
    job_id: str
    record_id: int
    task_id: int | None
    process_instance_id: int | None
    project_code: int
    process_definition_code: int
    scheduler_submission: Any
