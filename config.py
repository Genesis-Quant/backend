"""从环境变量加载后端配置。"""

import os
import warnings
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote_plus, urlsplit

from dotenv import load_dotenv

load_dotenv("../.env")
load_dotenv(".env", override=True)

PROD = os.getenv("PROD") == "true"

if PROD:
    warnings.filterwarnings("ignore")


def positive_integer_environment(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是正整数") from error
    if value < 1:
        raise RuntimeError(f"{name} 必须是正整数")
    return value


def boolean_environment(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} 必须是 True 或 False")
    return value == "true"


class ArenaSettings:
    """Arena 工作流共享输入和结果存储配置。"""

    SHARED_DIR = Path(os.getenv("ARENA_SHARED_DIR", "/shared")).resolve()
    SHARED_CLOUD = boolean_environment("ARENA_SHARED_CLOUD")

    @classmethod
    def validate(cls) -> None:
        if not cls.SHARED_CLOUD:
            return
        from runtime.utils.storage import ObjectStorage

        with ObjectStorage.from_env():
            pass


class DatabaseSettings:
    """PostgreSQL 连接配置。"""

    HOST = os.getenv("PGSQL_HOST", "127.0.0.1")
    PORT = int(os.getenv("PGSQL_PORT", "5432"))
    USERNAME = os.getenv("PGSQL_USER", "postgres")
    PASSWORD = os.getenv("PGSQL_PASSWORD")
    DATABASE = os.getenv("ARENA_DATABASE", "arena_runtime")
    URL = f"postgresql+psycopg://{quote_plus(USERNAME)}:{quote_plus(PASSWORD or '')}@{HOST}:{PORT}/{DATABASE}"

    @classmethod
    def validate(cls) -> None:
        if not cls.PASSWORD:
            raise RuntimeError("缺少 PostgreSQL 配置：PGSQL_PASSWORD")


class AuthenticationSettings:
    """用户认证配置。"""

    JWT_SECRET = os.getenv("ARENA_JWT_SECRET", "")
    JWT_EXPIRE_DAYS = int(os.getenv("ARENA_JWT_EXPIRE_DAYS", "30"))


class MCPSettings:
    """MCP Streamable HTTP 对外地址与传输安全配置。"""

    PUBLIC_URL = os.getenv("ARENA_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    ENDPOINT_URL = f"{PUBLIC_URL}/mcp"

    @classmethod
    def validate(cls) -> None:
        parsed = urlsplit(cls.PUBLIC_URL)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise RuntimeError("ARENA_PUBLIC_URL 必须是只包含协议和主机的 HTTP(S) 地址")

    @classmethod
    def allowed_hosts(cls) -> list[str]:
        cls.validate()
        return [
            urlsplit(cls.PUBLIC_URL).netloc,
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
            "[::1]",
            "[::1]:*",
        ]

    @classmethod
    def allowed_origins(cls) -> list[str]:
        cls.validate()
        return [cls.PUBLIC_URL, "http://127.0.0.1:*", "http://localhost:*"]


class DolphinSchedulerSettings:
    """DolphinScheduler 连接、工作流和任务轮询配置。"""

    HOST = os.getenv("DOLPHINSCHEDULER_HOST", "127.0.0.1")
    BASE_URL = f"http://{HOST}:12345/dolphinscheduler"
    PYTHON_GATEWAY_PORT = 25333
    PYTHON_GATEWAY_AUTH_TOKEN = os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_TOKEN", "")
    USERNAME = os.getenv("DOLPHINSCHEDULER_USERNAME", "arena-scheduler")
    PASSWORD = os.getenv("DOLPHINSCHEDULER_PASSWORD", "")
    PROJECT_NAME = os.getenv("DOLPHINSCHEDULER_PROJECT_NAME", "arena-runtime")
    WORKFLOW_NAME = "incremental-update"
    INCREMENTAL_TASK_GROUP_NAME = "tushare-api"
    WORKER_GROUP = os.getenv("DOLPHINSCHEDULER_WORKER_GROUP", "default")
    TENANT_CODE = os.getenv("DOLPHINSCHEDULER_TENANT_CODE", "default")
    RUNTIME_COMMAND = "/opt/arena-runtime/.venv/bin/core-manage"
    POLL_INTERVAL_SECONDS = float(os.getenv("DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS", "5"))
    POLL_BATCH_SIZE = int(os.getenv("DOLPHINSCHEDULER_POLL_BATCH_SIZE", "100"))
    APPLICATION_WORKFLOW_NAMES: ClassVar[dict[str, str]] = {
        "query": "query",
        "factor": "factor",
        "backtest": "backtest",
    }
    APPLICATION_TASK_GROUP_NAMES: ClassVar[dict[str, str]] = {
        "query": "query-tasks",
        "factor": "factor-tasks",
        "backtest": "backtest-tasks",
    }
    APPLICATION_TASK_GROUP_SIZES: ClassVar[dict[str, int]] = {
        "query": positive_integer_environment("DOLPHINSCHEDULER_QUERY_TASK_GROUP_SIZE", 1),
        "factor": positive_integer_environment("DOLPHINSCHEDULER_FACTOR_TASK_GROUP_SIZE", 1),
        "backtest": positive_integer_environment("DOLPHINSCHEDULER_BACKTEST_TASK_GROUP_SIZE", 1),
    }

    @classmethod
    def validate(cls) -> None:
        missing = [
            name
            for name, value in {
                "DOLPHINSCHEDULER_PASSWORD": cls.PASSWORD,
                "DOLPHINSCHEDULER_PYTHON_GATEWAY_TOKEN": cls.PYTHON_GATEWAY_AUTH_TOKEN,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"缺少 DolphinScheduler 配置：{', '.join(missing)}"
            )

    @classmethod
    def configure_sdk_environment(cls) -> None:
        cls.validate()
        os.environ.update({
            "PYDS_JAVA_GATEWAY_ADDRESS": cls.HOST,
            "PYDS_JAVA_GATEWAY_PORT": str(cls.PYTHON_GATEWAY_PORT),
            "PYDS_JAVA_GATEWAY_AUTH_TOKEN": cls.PYTHON_GATEWAY_AUTH_TOKEN,
            "PYDS_USER_NAME": cls.USERNAME,
            "PYDS_USER_PASSWORD": cls.PASSWORD,
            "PYDS_USER_TENANT": cls.TENANT_CODE,
            "PYDS_WORKFLOW_USER": cls.USERNAME,
            "PYDS_WORKFLOW_PROJECT": cls.PROJECT_NAME,
            "PYDS_WORKFLOW_WORKER_GROUP": cls.WORKER_GROUP,
            "PYDS_WORKFLOW_TIME_ZONE": "Asia/Shanghai",
        })
