"""Query task API schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.apps.query.schema import FactorQuery

from apps.utils.results import ResultFile
from apps.utils.validation import validate_outputs


class QueryTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_query: FactorQuery
    output: list[Literal["source_data", "computed_data", "filtered_data", "data"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class QueryTaskSubmitted(BaseModel):
    task_id: int


class QueryResultFile(ResultFile):
    name: Literal["source_data", "computed_data", "filtered_data", "data"]
