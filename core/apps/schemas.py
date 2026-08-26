"""Schemas shared by multiple applications."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.utils.validation import strip_text

type SortOrder = Literal["asc", "desc"]


class ProjectPage[T: BaseModel](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
    all_total: int | None = Field(default=None, exclude_if=lambda value: value is None)
    limit: int | None = Field(default=None, exclude_if=lambda value: value is None)


class WorkflowSubmitted(BaseModel):
    workspace_id: int
    workflow_instance_id: int


class WorkflowReference(BaseModel):
    workflow_instance_id: int | None
    state: str


class WorkflowSummary[T: BaseModel | dict[str, Any]](BaseModel):
    workspace_id: int
    workflow_instance_id: int | None
    state: str
    error: str | None
    parameters: T
    updated_at: datetime


class DraftVersionSummary[T: BaseModel | dict[str, Any]](WorkflowSummary[T]):
    id: int
    version: int
    saved: bool


class BatchRunItem[T: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_id: str = Field(min_length=1, max_length=64)
    remark: str = Field(default="", max_length=512)
    parameters: T

    validate_client_id = field_validator("client_id")(strip_text)
    validate_remark = field_validator("remark")(strip_text)


class BatchRunRequest[T: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[BatchRunItem[T]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_client_ids(self) -> "BatchRunRequest[T]":
        client_ids = [item.client_id for item in self.items]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("client_id 不能重复")
        return self


class BatchRunAccepted(BaseModel):
    client_id: str
    workspace_id: int
