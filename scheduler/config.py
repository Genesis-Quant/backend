"""Scheduler configuration loaded from the Arena environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ARENA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ARENA_ROOT / ".env"


def _positive_integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数，当前值为 {raw_value!r}") from error
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0，当前值为 {value}")
    return value


def _nonnegative_integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数，当前值为 {raw_value!r}") from error
    if value < 0:
        raise ValueError(f"{name} 不能小于 0，当前值为 {value}")
    return value


@dataclass(frozen=True, slots=True)
class DolphinSchedulerSettings:
    """Connection and workflow settings."""

    base_url: str
    username: str
    password: str
    project_name: str
    workflow_name: str
    task_group_prefix: str
    worker_group: str
    tenant_code: str
    runtime_command: str
    request_timeout_seconds: float
    incremental_threads: int
    incremental_throttle: int

    @classmethod
    def from_environment(cls) -> DolphinSchedulerSettings:
        """Build settings from process environment and the root local `.env`."""
        load_dotenv(DEFAULT_ENV_FILE, override=False)
        return cls(
            base_url=os.getenv(
                "DOLPHINSCHEDULER_BASE_URL",
                "http://127.0.0.1:12345/dolphinscheduler",
            ).rstrip("/"),
            username=os.getenv("DOLPHINSCHEDULER_USERNAME", "admin"),
            password=os.getenv(
                "DOLPHINSCHEDULER_PASSWORD",
                "dolphinscheduler123",
            ),
            project_name=os.getenv(
                "DOLPHINSCHEDULER_PROJECT_NAME",
                "arena-runtime",
            ),
            workflow_name=os.getenv(
                "DOLPHINSCHEDULER_WORKFLOW_NAME",
                "incremental-update",
            ),
            task_group_prefix=os.getenv(
                "DOLPHINSCHEDULER_TASK_GROUP_PREFIX",
                "arena-incremental",
            ),
            worker_group=os.getenv(
                "DOLPHINSCHEDULER_WORKER_GROUP",
                "default",
            ),
            tenant_code=os.getenv(
                "DOLPHINSCHEDULER_TENANT_CODE",
                "default",
            ),
            runtime_command=os.getenv(
                "DOLPHINSCHEDULER_RUNTIME_COMMAND",
                "/opt/arena-runtime/.venv/bin/core-manage",
            ),
            request_timeout_seconds=float(
                os.getenv("DOLPHINSCHEDULER_REQUEST_TIMEOUT_SECONDS", "30"),
            ),
            incremental_threads=_positive_integer(
                "INCREMENTAL_UPDATE_THREADS",
                1,
            ),
            incremental_throttle=_nonnegative_integer(
                "INCREMENTAL_UPDATE_THROTTLE",
                8,
            ),
        )
