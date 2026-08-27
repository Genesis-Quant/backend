"""Backtest workflow, strategy project, and version schemas."""

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime import OptimizationAlgorithm, OptimizationParameters, OptimizationSettings, SensitivityParameters
from core.apps.schemas import DraftVersionSummary
from core.utils.dsl import DslCatalog
from core.utils.validation import (
    normalize_text,
    strip_text,
)

type BacktestOutput = Literal[
    "trade_details",
    "daily_positions",
    "daily_portfolios",
    "daily_trading_statistics",
]
type OptimizationOutput = OptimizationAlgorithm
type SensitivityOutput = Literal["results"]
type BacktestProjectSortField = Literal[
    "id",
    "title",
    "latest_version",
    "totalReturn",
    "annualReturn",
    "sharpeRatio",
    "annualVolatility",
    "maxDrawdown",
    "dailyWinningRate",
    "updated_at",
]


class BacktestCatalog(DslCatalog):
    benchmark_codes: list[str]


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
    draft: DraftVersionSummary[dict[str, Any]]
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

    validate_remark = field_validator("remark")(strip_text)


class BacktestVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    remark: str = Field(min_length=1, max_length=512)
    validate_remark = field_validator("remark")(normalize_text)


class BacktestVersionResponse(BaseModel):
    id: int
    project_id: int
    workflow_workspace_id: int
    workflow_instance_id: int | None
    version: int
    saved: bool
    is_current: bool
    remark: str
    parameters: dict[str, Any]
    summary: dict[str, float | int | None] | None
    created_at: datetime
    updated_at: datetime


class BacktestVersionListItem(BaseModel):
    id: int
    version: int
    saved: bool
    is_current: bool
    remark: str
    workflow_instance_id: int | None
    created_at: datetime


class BacktestOptimizationCreate(OptimizationSettings):
    """基于一个已保存回测版本创建参数调优报告。"""


class BacktestOptimizationResponse(BaseModel):
    id: int
    project_id: int
    version: int
    workflow_workspace_id: int
    workflow_instance_id: int | None
    state: str
    error: str | None
    parameters: OptimizationParameters
    created_at: datetime
    updated_at: datetime


class BacktestOptimizationPage(BaseModel):
    items: list[BacktestOptimizationResponse]
    total: int
    page: int
    page_size: int


class BatchAnalysisType(StrEnum):
    FEE_ANALYSIS = "fee_analysis"
    SENSITIVITY = "sensitivity"


class BatchResearchItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    parameters: dict[str, Any]


class BatchResearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    analysis_type: BatchAnalysisType = Field(strict=False)
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
        if any(not isfinite(rate) or rate < 0 or rate > 1 for rate in value):
            raise ValueError("手续费率必须位于 0 到 1 之间")
        rates = sorted(set(value))
        if not rates:
            raise ValueError("至少提供一个手续费率")
        return rates


class BatchResearchListItem(BaseModel):
    id: int
    analysis_type: BatchAnalysisType
    analysis_type_label: str
    project_id: int
    version: int
    description: str
    workflow_workspace_id: int
    workflow_instance_id: int | None
    state: str
    requested_count: int
    completed_count: int
    failed_count: int
    error: str | None
    parameters: SensitivityParameters
    created_at: datetime
    updated_at: datetime


class BatchResearchListResponse(BaseModel):
    items: list[BatchResearchListItem]
    total: int
    page: int
    page_size: int


class BatchResearchResponse(BatchResearchListItem):
    pass
