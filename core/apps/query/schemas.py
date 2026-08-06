"""Query workflow and project API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime.apps.query.schema import FactorQuery

from core.apps.schemas import WorkflowReference, WorkflowSummary
from core.utils.validation import normalize_text, validate_outputs

type QueryOutput = Literal["source_data", "computed_data", "filtered_data", "data"]


class QueryWorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_query: FactorQuery
    output: list[QueryOutput] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class QueryProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class QueryProjectItem(BaseModel):
    id: int
    title: str
    current: WorkflowSummary[FactorQuery] | None
    created_at: datetime
    updated_at: datetime


class QueryProjectListItem(BaseModel):
    id: int
    title: str
    current: WorkflowReference | None
    updated_at: datetime
