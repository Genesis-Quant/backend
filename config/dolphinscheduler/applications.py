"""Reusable Runtime application workflow definitions."""

from __future__ import annotations

from typing import Any

from config.dolphinscheduler.domain import APPLICATIONS
from config.dolphinscheduler.errors import DolphinSchedulerError
from config.settings import DolphinSchedulerSettings

DEFAULT_OUTPUT = {
    "query": "data",
    "factor": "processed_data",
    "backtest": "return_summary",
}


def create_application_workflows(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    """Create or update query, factor, and backtest workflows."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    current_settings.configure_sdk_environment()

    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.tasks.shell import Shell

        workflow_codes: dict[str, int] = {}
        for application in APPLICATIONS:
            workflow = Workflow(
                name=current_settings.application_workflow_names[application],
                description=f"Arena Runtime {application} 共享目录任务",
                user=current_settings.username,
                project=current_settings.project_name,
                worker_group=current_settings.worker_group,
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
                        f"exec {current_settings.runtime_command} "
                        f'apps {application} --input-file "${{input_file}}" '
                        "--output ${output}"
                    ),
                    description=(
                        f"从共享目录 input.json 运行 {application}，"
                        "并将 Parquet 写回任务 output 目录"
                    ),
                    worker_group=current_settings.worker_group,
                )
            workflow_codes[application] = int(workflow.submit())
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error

    return {
        "project_name": current_settings.project_name,
        "workflows": {
            application: {
                "name": current_settings.application_workflow_names[application],
                "code": workflow_codes[application],
            }
            for application in APPLICATIONS
        },
    }
