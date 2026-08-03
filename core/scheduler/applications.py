"""Reusable Runtime application workflow definitions."""

from __future__ import annotations

from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.domain import APPLICATIONS
from core.scheduler.errors import DolphinSchedulerError

DEFAULT_OUTPUT = {
    "query": "data",
    "factor": "processed_data",
    "backtest": "return_summary",
}


def create_application_workflows() -> dict[str, Any]:
    """Create or update query, factor, and backtest workflows."""
    DolphinSchedulerSettings.configure_sdk_environment()

    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.tasks.shell import Shell

        workflow_codes: dict[str, int] = {}
        for application in APPLICATIONS:
            workflow = Workflow(
                name=DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[application],
                description=f"Arena Runtime {application} 共享目录任务",
                user=DolphinSchedulerSettings.USERNAME,
                project=DolphinSchedulerSettings.PROJECT_NAME,
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                execution_type="PARALLEL",
                release_state="online",
                param={
                    "input_file": f"/shared/{application}/input.json",
                    "job_id": "definition-default",
                    "output": DEFAULT_OUTPUT[application],
                },
            )
            with workflow:
                Shell(
                    name=application,
                    command=(
                        f"exec {DolphinSchedulerSettings.RUNTIME_COMMAND} "
                        f'apps {application} --input-file "${{input_file}}" '
                        "--output ${output}"
                    ),
                    description=(
                        f"从共享目录 input.json 运行 {application}，"
                        "并将 Parquet 写回任务 output 目录"
                    ),
                    worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                )
            workflow_codes[application] = int(workflow.submit())
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error

    return {
        "project_name": DolphinSchedulerSettings.PROJECT_NAME,
        "workflows": {
            application: {
                "name": DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[application],
                "code": workflow_codes[application],
            }
            for application in APPLICATIONS
        },
    }
