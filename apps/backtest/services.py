"""Backtest task submission and result access."""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from apps.backtest.models import BacktestTask
from apps.tasks.services import TaskExecutionService
from apps.utils.results import result_files, result_path

OUTPUT_FILES = {
    "trade_details": "trade_details.parquet",
    "daily_positions": "daily_positions.parquet",
    "daily_portfolios": "daily_portfolios.parquet",
    "return_summary": "return_summary.parquet",
    "daily_trading_statistics": "daily_trading_statistics.parquet",
    "engine_stat": "engine_stat.parquet",
}


def submit_backtest_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> BacktestTask:
    return TaskExecutionService("backtest", BacktestTask).submit(session, user_id, payload, outputs)


def backtest_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, BacktestTask, OUTPUT_FILES)


def backtest_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, BacktestTask, OUTPUT_FILES)
