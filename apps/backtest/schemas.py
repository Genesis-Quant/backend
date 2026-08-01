"""Backtest task API schemas."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from core.apps.backtest.schema import BacktestParameters

from apps.utils.results import ResultFile
from apps.utils.validation import validate_outputs


class BacktestTaskCreate(BacktestParameters):
    output: list[Literal["trade_details", "daily_positions", "daily_portfolios", "return_summary", "daily_trading_statistics", "engine_stat"]] = Field(min_length=1)

    validate_output = field_validator("output")(validate_outputs)


class BacktestTaskSubmitted(BaseModel):
    task_id: int


class BacktestResultFile(ResultFile):
    name: Literal["trade_details", "daily_positions", "daily_portfolios", "return_summary", "daily_trading_statistics", "engine_stat"]
