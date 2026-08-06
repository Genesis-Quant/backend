"""Schemas shared by multiple applications."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProjectPage[T: BaseModel](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
    limit: int | None = Field(default=None, exclude_if=lambda value: value is None)


class WorkflowSubmitted(BaseModel):
    record_id: int
    workflow_instance_id: int


class WorkflowReference(BaseModel):
    workflow_instance_id: int | None
    state: str


class WorkflowSummary[T: BaseModel | dict[str, Any]](BaseModel):
    record_id: int
    workflow_instance_id: int | None
    state: str
    error: str | None
    parameters: T
    updated_at: datetime
