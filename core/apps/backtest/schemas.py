"""Backtest workflow, strategy project, and version schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime import BacktestParameters

from core.utils.results import ResultFile
from core.utils.validation import normalize_text, validate_outputs


class BacktestWorkflowCreate(BacktestParameters):
    output: list[Literal["trade_details", "daily_positions", "daily_portfolios", "return_summary", "daily_trading_statistics", "engine_stat"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class BacktestWorkflowSubmitted(BaseModel):
    record_id: int
    workflow_instance_id: int


class BacktestResultFile(ResultFile):
    name: Literal["trade_details", "daily_positions", "daily_portfolios", "return_summary", "daily_trading_statistics", "engine_stat"]


class BacktestProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=128)
    validate_title = field_validator("title")(normalize_text)


class BacktestProjectUpdate(BacktestProjectCreate):
    pass


class BacktestWorkflowSummary(BaseModel):
    record_id: int
    workflow_instance_id: int | None
    state: str
    error: str | None
    parameters: dict[str, Any]
    updated_at: datetime


class BacktestProjectItem(BaseModel):
    id: int
    title: str
    latest_version: int | None
    latest_summary: dict[str, float | int | None] | None
    draft: BacktestWorkflowSummary | None
    created_at: datetime
    updated_at: datetime


class BacktestProjectPage(BaseModel):
    items: list[BacktestProjectItem]
    page: int
    page_size: int
    total: int


class BacktestVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_instance_id: int = Field(gt=0)
    remark: str = Field(default="", max_length=512)
    summary: dict[str, float | int | None] = Field(min_length=1)

    @field_validator("remark")
    @classmethod
    def normalize_remark(cls, value: str) -> str:
        return value.strip()


class BacktestVersionResponse(BaseModel):
    id: int
    project_id: int
    workflow_instance_id: int
    version: int
    remark: str
    parameters: dict[str, Any]
    summary: dict[str, float | int | None]
    created_at: datetime
