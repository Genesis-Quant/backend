"""Shared DolphinScheduler application and execution-state constants."""

from typing import Literal, TypeAlias

ApplicationName: TypeAlias = Literal["query", "factor", "backtest"]
APPLICATIONS: tuple[ApplicationName, ...] = ("query", "factor", "backtest")
APPLICATION_START_PARAMETERS = (
    "input_file",
    "output_dir",
    "job_id",
    "output",
    "cloud",
)
INCREMENTAL_START_PARAMETERS = (
    "job_id",
    "output_dir",
    "workers",
    "channel",
)
FAILURE_STATES = frozenset({"FAILURE", "STOP", "KILL"})
TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "STOP", "KILL", "FORCED_SUCCESS", "SUBMIT_FAILED"})


def validate_start_parameters(
    parameters: dict[str, str],
    required: tuple[str, ...],
) -> dict[str, str]:
    """校验一次工作流启动参数完整且没有协议外字段。"""
    missing = [name for name in required if not parameters.get(name, "").strip()]
    unexpected = sorted(set(parameters) - set(required))
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"缺少参数: {missing}")
        if unexpected:
            details.append(f"未知参数: {unexpected}")
        raise ValueError("工作流启动参数不符合协议，" + "；".join(details))
    return parameters
