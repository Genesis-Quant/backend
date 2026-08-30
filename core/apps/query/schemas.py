"""Query workflow and project API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.apps.schemas import WorkflowReference, WorkflowSummary
from core.utils.dsl_source import FactorQueryRequest
from core.utils.validation import normalize_text

type QueryOutput = Literal["source_data", "computed_data", "filtered_data", "data"]
type QueryProjectSortField = Literal[
    "id",
    "title",
    "state",
    "workflow_instance_id",
    "updated_at",
]


class QueryProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class QueryProjectItem(BaseModel):
    id: int
    title: str
    current: WorkflowSummary[FactorQueryRequest] | None
    created_at: datetime
    updated_at: datetime


class QueryProjectListItem(BaseModel):
    id: int
    title: str
    current: WorkflowReference | None
    updated_at: datetime
