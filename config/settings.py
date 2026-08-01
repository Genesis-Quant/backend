"""Environment-backed Backend settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ARENA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ARENA_ROOT / ".env"


@dataclass(frozen=True)
class AuthenticationSettings:
    jwt_secret: str
    jwt_expire_days: int

    @classmethod
    def from_environment(cls) -> AuthenticationSettings:
        load_dotenv(DEFAULT_ENV_FILE, override=False)
        try:
            jwt_expire_days = int(os.getenv("ARENA_JWT_EXPIRE_DAYS", "30"))
        except ValueError as error:
            raise ValueError("ARENA_JWT_EXPIRE_DAYS 必须是正整数") from error
        settings = cls(
            jwt_secret=os.getenv("ARENA_JWT_SECRET", ""),
            jwt_expire_days=jwt_expire_days,
        )
        if len(settings.jwt_secret) < 32:
            raise ValueError("ARENA_JWT_SECRET 必须至少包含 32 个字符")
        if settings.jwt_expire_days <= 0:
            raise ValueError("ARENA_JWT_EXPIRE_DAYS 必须是正整数")
        return settings


@dataclass(frozen=True)
class DolphinSchedulerSettings:
    gateway_address: str
    gateway_port: int
    gateway_auth_token: str
    api_base_url: str
    username: str
    password: str
    project_name: str
    workflow_name: str
    query_workflow_name: str
    factor_workflow_name: str
    backtest_workflow_name: str
    incremental_task_group_name: str
    incremental_task_group_size: int
    worker_group: str
    tenant_code: str
    runtime_command: str
    shared_dir: Path
    poll_interval_seconds: float
    poll_batch_size: int

    @classmethod
    def from_environment(cls) -> DolphinSchedulerSettings:
        load_dotenv(DEFAULT_ENV_FILE, override=False)
        gateway_auth_token = os.getenv(
            "DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN",
            "",
        )
        if not gateway_auth_token:
            raise ValueError("DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN 不能为空")
        api_base_url = os.getenv(
            "DOLPHINSCHEDULER_BASE_URL",
            "http://127.0.0.1:12345/dolphinscheduler",
        ).rstrip("/")
        if not api_base_url:
            raise ValueError("DOLPHINSCHEDULER_BASE_URL 不能为空")

        settings = cls(
            gateway_address=os.getenv(
                "DOLPHINSCHEDULER_PYTHON_GATEWAY_ADDRESS",
                "127.0.0.1",
            ),
            gateway_port=int(
                os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT", "25333")
            ),
            gateway_auth_token=gateway_auth_token,
            api_base_url=api_base_url,
            username=os.getenv("DOLPHINSCHEDULER_USERNAME", "arena-scheduler"),
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
            query_workflow_name=os.getenv(
                "DOLPHINSCHEDULER_QUERY_WORKFLOW_NAME",
                "query",
            ),
            factor_workflow_name=os.getenv(
                "DOLPHINSCHEDULER_FACTOR_WORKFLOW_NAME",
                "factor",
            ),
            backtest_workflow_name=os.getenv(
                "DOLPHINSCHEDULER_BACKTEST_WORKFLOW_NAME",
                "backtest",
            ),
            incremental_task_group_name=os.getenv(
                "DOLPHINSCHEDULER_INCREMENTAL_TASK_GROUP_NAME",
                "tushare-api",
            ),
            incremental_task_group_size=int(
                os.getenv(
                    "DOLPHINSCHEDULER_INCREMENTAL_TASK_GROUP_SIZE",
                    "1",
                )
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
            shared_dir=Path(os.getenv("ARENA_SHARED_DIR", "/shared")).resolve(),
            poll_interval_seconds=float(os.getenv("DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS", "5")),
            poll_batch_size=int(os.getenv("DOLPHINSCHEDULER_POLL_BATCH_SIZE", "100")),
        )
        if settings.gateway_port <= 0:
            raise ValueError("DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT 必须大于 0")
        if settings.incremental_task_group_size <= 0:
            raise ValueError(
                "DOLPHINSCHEDULER_INCREMENTAL_TASK_GROUP_SIZE 必须大于 0"
            )
        if settings.poll_interval_seconds <= 0:
            raise ValueError("DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS 必须大于 0")
        if settings.poll_batch_size <= 0:
            raise ValueError("DOLPHINSCHEDULER_POLL_BATCH_SIZE 必须大于 0")
        return settings

    @property
    def application_workflow_names(self) -> dict[str, str]:
        return {
            "query": self.query_workflow_name,
            "factor": self.factor_workflow_name,
            "backtest": self.backtest_workflow_name,
        }

    def configure_sdk_environment(self) -> None:
        os.environ.update(
            {
                "PYDS_JAVA_GATEWAY_ADDRESS": self.gateway_address,
                "PYDS_JAVA_GATEWAY_PORT": str(self.gateway_port),
                "PYDS_JAVA_GATEWAY_AUTH_TOKEN": self.gateway_auth_token,
                "PYDS_USER_NAME": self.username,
                "PYDS_USER_PASSWORD": self.password,
                "PYDS_USER_TENANT": self.tenant_code,
                "PYDS_WORKFLOW_USER": self.username,
                "PYDS_WORKFLOW_PROJECT": self.project_name,
                "PYDS_WORKFLOW_WORKER_GROUP": self.worker_group,
                "PYDS_WORKFLOW_TIME_ZONE": "Asia/Shanghai",
            }
        )
