"""Scheduler domain constants and control actions."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypeAlias

ApplicationName: TypeAlias = Literal["query", "factor", "backtest"]
JobKind: TypeAlias = Literal[
    "query",
    "factor",
    "backtest",
    "incremental-update",
]

APPLICATIONS: tuple[ApplicationName, ...] = ("query", "factor", "backtest")
JOB_KINDS: tuple[JobKind, ...] = (*APPLICATIONS, "incremental-update")
APPLICATION_OUTPUTS: dict[ApplicationName, tuple[str, ...]] = {
    "query": ("source_data", "computed_data", "filtered_data", "data"),
    "factor": ("processed_data", "information_coefficient", "group_returns"),
    "backtest": (
        "trade_details",
        "daily_positions",
        "daily_portfolios",
        "return_summary",
        "daily_trading_statistics",
        "engine_stat",
    ),
}

TERMINAL_STATES = frozenset(
    {
        "SUCCESS",
        "FAILURE",
        "STOP",
        "KILL",
        "FORCED_SUCCESS",
    }
)


class JobAction(StrEnum):
    """Arena-facing process instance actions."""

    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RERUN = "rerun"
    RETRY_FAILED = "retry-failed"

    @property
    def execute_type(self) -> str:
        return {
            JobAction.STOP: "STOP",
            JobAction.PAUSE: "PAUSE",
            JobAction.RESUME: "RECOVER_SUSPENDED_PROCESS",
            JobAction.RERUN: "REPEAT_RUNNING",
            JobAction.RETRY_FAILED: "START_FAILURE_TASK_PROCESS",
        }[self]

    @property
    def creates_attempt(self) -> bool:
        return self in {JobAction.RERUN, JobAction.RETRY_FAILED}


class TaskAction(StrEnum):
    """Arena-facing task instance actions."""

    STOP = "stop"
    FORCE_SUCCESS = "force-success"
