"""Shared DolphinScheduler application and execution-state constants."""

from typing import Literal, TypeAlias

ApplicationName: TypeAlias = Literal["query", "factor", "backtest"]
APPLICATIONS: tuple[ApplicationName, ...] = ("query", "factor", "backtest")
FAILURE_STATES = frozenset({"FAILURE", "STOP", "KILL"})
TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "STOP", "KILL", "FORCED_SUCCESS", "SUBMIT_FAILED"})
