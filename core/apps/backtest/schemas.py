"""Backtest workflow, strategy project, and version schemas."""

from datetime import datetime
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
