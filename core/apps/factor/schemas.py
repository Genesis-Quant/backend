"""Factor workflow, project, version, and DSL catalog schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime.apps.factor.schema import FactorAnalysisParameters

from core.apps.schemas import WorkflowSummary
from core.utils.validation import (
    normalize_text,
    strip_text,
    validate_outputs,
)

type FactorOutput = Literal["processed_data", "information_coefficient", "group_returns"]


class FactorWorkflowCreate(FactorAnalysisParameters):
    output: list[FactorOutput] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class FactorProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class FactorProjectUpdate(FactorProjectCreate):
    pass


class FactorProjectItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    draft: WorkflowSummary[dict[str, Any]] | None
    created_at: datetime
    updated_at: datetime


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


class FactorProjectListItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    latest_metric: FactorMetricSummary | None
    updated_at: datetime


class FactorVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_instance_id: int = Field(gt=0)
    remark: str = Field(default="", max_length=512)
    metrics: FactorMetrics

    validate_remark = field_validator("remark")(strip_text)


class FactorVersionResponse(BaseModel):
    id: int
    project_id: int
    workflow_instance_id: int
    version: int
    remark: str
    parameters: dict[str, Any]
    metrics: FactorMetrics
    created_at: datetime


class FactorVersionListItem(BaseModel):
    id: int
    version: int
    remark: str
    created_at: datetime
