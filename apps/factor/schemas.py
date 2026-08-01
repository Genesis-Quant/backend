"""Factor analysis task API schemas."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from core.apps.factor.schema import FactorAnalysisParameters

from apps.utils.results import ResultFile
from apps.utils.validation import validate_outputs


class FactorTaskCreate(FactorAnalysisParameters):
    output: list[Literal["processed_data", "information_coefficient", "group_returns"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class FactorTaskSubmitted(BaseModel):
    task_id: int


class FactorResultFile(ResultFile):
    name: Literal["processed_data", "information_coefficient", "group_returns"]
