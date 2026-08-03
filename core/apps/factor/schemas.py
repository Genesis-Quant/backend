"""Factor workflow, project, version, and DSL catalog schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime.apps.factor.schema import FactorAnalysisParameters

from core.utils.results import ResultFile
from core.utils.validation import normalize_text, validate_outputs


class FactorWorkflowCreate(FactorAnalysisParameters):
    output: list[Literal["processed_data", "information_coefficient", "group_returns"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class FactorWorkflowSubmitted(BaseModel):
    record_id: int
    workflow_instance_id: int


class FactorResultFile(ResultFile):
    name: Literal["processed_data", "information_coefficient", "group_returns"]


class FactorProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class FactorProjectUpdate(FactorProjectCreate):
    pass


class FactorWorkflowSummary(BaseModel):
    record_id: int
    workflow_instance_id: int | None
    state: str
    error: str | None
    parameters: dict[str, Any]
    updated_at: datetime


class FactorProjectItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    latest_metrics: dict[str, Any] | None
    draft: FactorWorkflowSummary | None
    created_at: datetime
    updated_at: datetime


class FactorProjectPage(BaseModel):
    items: list[FactorProjectItem]
    page: int
    page_size: int
    total: int


class FactorMetricSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observations: int = Field(ge=0)
    ic_mean: float | None = Field(default=None, allow_inf_nan=False)
    ic_std: float | None = Field(default=None, allow_inf_nan=False)
    ic_ir: float | None = Field(default=None, allow_inf_nan=False)
    ic_positive_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    rank_ic_mean: float | None = Field(default=None, allow_inf_nan=False)
    rank_ic_std: float | None = Field(default=None, allow_inf_nan=False)
    rank_ic_ir: float | None = Field(default=None, allow_inf_nan=False)
    rank_ic_positive_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    long_short_cumulative_return: float | None = Field(default=None, allow_inf_nan=False)
    long_short_annual_return: float | None = Field(default=None, allow_inf_nan=False)
    long_short_annual_volatility: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    long_short_sharpe: float | None = Field(default=None, allow_inf_nan=False)
    long_short_max_drawdown: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


FactorMetrics = dict[str, dict[str, FactorMetricSummary]]


class FactorVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_instance_id: int = Field(gt=0)
    remark: str = Field(default="", max_length=512)
    metrics: FactorMetrics

    @field_validator("remark")
    @classmethod
    def normalize_remark(cls, value: str) -> str:
        return value.strip()


class FactorVersionResponse(BaseModel):
    id: int
    project_id: int
    workflow_instance_id: int
    version: int
    remark: str
    parameters: dict[str, Any]
    metrics: FactorMetrics
    created_at: datetime
