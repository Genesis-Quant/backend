"""Backtest workflow, strategy project, and version schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime import BacktestParameters

from core.apps.schemas import WorkflowSummary
from core.utils.validation import (
    normalize_text,
    strip_text,
    validate_outputs,
)

type BacktestOutput = Literal[
    "trade_details",
    "daily_positions",
    "daily_portfolios",
    "return_summary",
    "daily_trading_statistics",
    "engine_stat",
]


class BacktestWorkflowCreate(BacktestParameters):
    output: list[BacktestOutput] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class BacktestProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class BacktestProjectUpdate(BacktestProjectCreate):
    pass


class BacktestProjectItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    draft: WorkflowSummary[dict[str, Any]] | None
    created_at: datetime
    updated_at: datetime


class BacktestProjectListItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    latest_summary: dict[str, float | int | None] | None
    updated_at: datetime


class BacktestVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_instance_id: int = Field(gt=0)
    remark: str = Field(default="", max_length=512)
    summary: dict[str, float | int | None] = Field(min_length=1)

    validate_remark = field_validator("remark")(strip_text)


class BacktestVersionResponse(BaseModel):
    id: int
    project_id: int
    workflow_instance_id: int
    version: int
    remark: str
    parameters: dict[str, Any]
    summary: dict[str, float | int | None]
    created_at: datetime


class BacktestVersionListItem(BaseModel):
    id: int
    version: int
    remark: str
    created_at: datetime


class BatchAnalysisType(StrEnum):
    FEE_ANALYSIS = "fee_analysis"
    SENSITIVITY = "sensitivity"


class BatchResearchItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    parameters: dict[str, Any]


class BatchResearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    analysis_type: BatchAnalysisType
    project_id: int = Field(gt=0)
    version: int = Field(gt=0)
    description: str = Field(default="", max_length=512)
    items: list[BatchResearchItemCreate] = Field(min_length=1, max_length=100)

class FeeAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rates: list[float] = Field(min_length=1, max_length=100)

    @field_validator("rates")
    @classmethod
    def normalize_rates(cls, value: list[float]) -> list[float]:
        if any(rate < 0 or rate > 1 for rate in value):
            raise ValueError("手续费率必须位于 0 到 1 之间")
        rates = sorted(set(value))
        if not rates:
            raise ValueError("至少提供一个手续费率")
        return rates


class BatchResearchItemResponse(BaseModel):
    id: int
    workflow_run_id: int
    workflow_instance_id: int | None
    state: str
    parameters: dict[str, Any]
    error: str | None


class BatchResearchListItem(BaseModel):
    id: int
    analysis_type: BatchAnalysisType
    analysis_type_label: str
    project_id: int
    version: int
    description: str
    state: str
    requested_count: int
    completed_count: int
    failed_count: int
    created_at: datetime


class BatchResearchListResponse(BaseModel):
    items: list[BatchResearchListItem]
    total: int
    page: int
    page_size: int


class BatchResearchResponse(BatchResearchListItem):
    error: str | None
    items: list[BatchResearchItemResponse]
