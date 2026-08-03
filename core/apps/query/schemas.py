"""Query task and project API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime.apps.query.schema import FactorQuery

from core.utils.dsl import DslCatalog
from core.utils.results import ResultFile
from core.utils.validation import normalize_text, validate_outputs


class QueryTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_query: FactorQuery
    output: list[Literal["source_data", "computed_data", "filtered_data", "data"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class QueryTaskSubmitted(BaseModel):
    task_id: int


class QueryResultFile(ResultFile):
    name: Literal["source_data", "computed_data", "filtered_data", "data"]


class QueryProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class QueryTaskSummary(BaseModel):
    record_id: int
    task_id: int | None
    state: str
    error: str | None
    parameters: FactorQuery
    updated_at: datetime


class QueryProjectItem(BaseModel):
    id: int
    title: str
    current: QueryTaskSummary | None
    created_at: datetime
    updated_at: datetime


class QueryProjectPage(BaseModel):
    items: list[QueryProjectItem]
    page: int
    page_size: int
    total: int
    limit: int


class QueryProjectSubmitted(BaseModel):
    record_id: int
    task_id: int
    reused: bool
