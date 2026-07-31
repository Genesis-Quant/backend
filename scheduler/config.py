"""DolphinScheduler SDK configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ARENA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ARENA_ROOT / ".env"


@dataclass(frozen=True)
class DolphinSchedulerSettings:
    gateway_address: str
    gateway_port: int
    gateway_auth_token: str
    username: str
    password: str
    project_name: str
    workflow_name: str
    worker_group: str
    tenant_code: str
    runtime_command: str
    incremental_threads: int
    incremental_throttle: int

    @classmethod
    def from_environment(cls) -> DolphinSchedulerSettings:
        load_dotenv(DEFAULT_ENV_FILE, override=False)
        gateway_auth_token = os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN", "")
        if not gateway_auth_token:
            raise ValueError("DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN 不能为空")
        settings = cls(
            gateway_address=os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_ADDRESS", "127.0.0.1"),
            gateway_port=int(os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT", "25333")),
            gateway_auth_token=gateway_auth_token,
            username=os.getenv("DOLPHINSCHEDULER_USERNAME", "arena-scheduler"),
            password=os.getenv("DOLPHINSCHEDULER_PASSWORD", "dolphinscheduler123"),
            project_name=os.getenv("DOLPHINSCHEDULER_PROJECT_NAME", "arena-runtime"),
            workflow_name=os.getenv("DOLPHINSCHEDULER_WORKFLOW_NAME", "incremental-update"),
            worker_group=os.getenv("DOLPHINSCHEDULER_WORKER_GROUP", "default"),
            tenant_code=os.getenv("DOLPHINSCHEDULER_TENANT_CODE", "default"),
            runtime_command=os.getenv(
                "DOLPHINSCHEDULER_RUNTIME_COMMAND",
                "/opt/arena-runtime/.venv/bin/core-manage",
            ),
            incremental_threads=int(os.getenv("INCREMENTAL_UPDATE_THREADS", "1")),
            incremental_throttle=int(os.getenv("INCREMENTAL_UPDATE_THROTTLE", "8")),
        )
        if settings.gateway_port <= 0:
            raise ValueError("DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT 必须大于 0")
        if settings.incremental_threads <= 0:
            raise ValueError("INCREMENTAL_UPDATE_THREADS 必须大于 0")
        if settings.incremental_throttle < 0:
            raise ValueError("INCREMENTAL_UPDATE_THROTTLE 不能小于 0")
        return settings

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
