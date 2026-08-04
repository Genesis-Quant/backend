"""Reusable Runtime application workflow definitions."""

from __future__ import annotations

from typing import Any

from config import ArenaSettings, DolphinSchedulerSettings
from core.scheduler.domain import APPLICATIONS
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.task_groups import ensure_application_task_groups

DEFAULT_OUTPUT = {
    "query": "data",
    "factor": "information_coefficient",
    "backtest": "daily_portfolios",
}


def create_application_workflows() -> dict[str, Any]:
    """Create or update query, factor, and backtest workflows."""
    DolphinSchedulerSettings.configure_sdk_environment()

    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.models.project import Project
        from pydolphinscheduler.models.user import User
        from pydolphinscheduler.tasks.shell import Shell

        User(name=DolphinSchedulerSettings.USERNAME).create_if_not_exists()
        Project(name=DolphinSchedulerSettings.PROJECT_NAME).create_if_not_exists(
            DolphinSchedulerSettings.USERNAME
        )
        task_groups = ensure_application_task_groups()
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
                    "output_cloud": (
                        "--output-cloud"
                        if ArenaSettings.SHARED_CLOUD
                        else "--no-output-cloud"
                    ),
                },
            )
            with workflow:
                Shell(
                    name=application,
                    command=(
                        f"exec {DolphinSchedulerSettings.RUNTIME_COMMAND} "
                        f'apps {application} --input-file "${{input_file}}" '
                        "--output ${output} ${output_cloud}"
                    ),
                    description=(
                        f"从共享目录 input.json 运行 {application}，"
                        "并将 Parquet 写回任务 output 目录"
                    ),
                    worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                    task_group_id=int(task_groups[application]["id"]),
                    task_group_priority=1,
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
                "task_group": {
                    "id": int(task_groups[application]["id"]),
                    "name": task_groups[application]["name"],
                    "group_size": int(task_groups[application]["groupSize"]),
                },
            }
            for application in APPLICATIONS
        },
    }
