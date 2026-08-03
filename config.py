"""从环境变量加载后端配置。"""

import os
import warnings
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv("../.env")
load_dotenv(".env", override=True)

PROD = os.getenv("PROD") == "true"

if PROD:
    warnings.filterwarnings("ignore")


class DatabaseSettings:
    """PostgreSQL 连接配置。"""

    HOST = os.getenv("PGSQL_HOST", "127.0.0.1")
    PORT = int(os.getenv("PGSQL_PORT", "5432"))
    USERNAME = os.getenv("PGSQL_USER", "postgres")
    PASSWORD = os.getenv("PGSQL_PASSWORD", "")
    DATABASE = os.getenv("PGSQL_DATABASE", "arena_runtime")
    URL = f"postgresql+psycopg://{quote_plus(USERNAME)}:{quote_plus(PASSWORD)}@{HOST}:{PORT}/{DATABASE}"


class AuthenticationSettings:
    """用户认证配置。"""

    JWT_SECRET = os.getenv("ARENA_JWT_SECRET")
    JWT_EXPIRE_DAYS = int(os.getenv("ARENA_JWT_EXPIRE_DAYS", "30"))


class DolphinSchedulerSettings:
    """DolphinScheduler 连接、工作流和任务轮询配置。"""

    BASE_URL = os.getenv("DOLPHINSCHEDULER_BASE_URL", "http://127.0.0.1:12345/dolphinscheduler").rstrip("/")
    PYTHON_GATEWAY_ADDRESS = os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_ADDRESS", "127.0.0.1")
    PYTHON_GATEWAY_PORT = int(os.getenv("DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT", "25333"))
    PYTHON_GATEWAY_AUTH_TOKEN = os.getenv("API_PYTHON_GATEWAY_AUTH_TOKEN")
    USERNAME = os.getenv("DOLPHINSCHEDULER_USERNAME", "arena-scheduler")
    PASSWORD = os.getenv("DOLPHINSCHEDULER_PASSWORD", "dolphinscheduler123")
    PROJECT_NAME = os.getenv("DOLPHINSCHEDULER_PROJECT_NAME", "arena-runtime")
    WORKFLOW_NAME = "incremental-update"
    INCREMENTAL_TASK_GROUP_NAME = "tushare-api"
    WORKER_GROUP = os.getenv("DOLPHINSCHEDULER_WORKER_GROUP", "default")
    TENANT_CODE = os.getenv("DOLPHINSCHEDULER_TENANT_CODE", "default")
    RUNTIME_COMMAND = os.getenv("DOLPHINSCHEDULER_RUNTIME_COMMAND", "/opt/arena-runtime/.venv/bin/core-manage")
    SHARED_DIR = Path(os.getenv("ARENA_SHARED_DIR", "/shared")).resolve()
    POLL_INTERVAL_SECONDS = float(os.getenv("DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS", "5"))
    POLL_BATCH_SIZE = int(os.getenv("DOLPHINSCHEDULER_POLL_BATCH_SIZE", "100"))
    APPLICATION_WORKFLOW_NAMES = {
        "query": "query",
        "factor": "factor",
        "backtest": "backtest",
    }

    @classmethod
    def configure_sdk_environment(cls) -> None:
        os.environ.update({
            "PYDS_JAVA_GATEWAY_ADDRESS": cls.PYTHON_GATEWAY_ADDRESS,
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
