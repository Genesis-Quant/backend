"""Construction shared by Runtime application workflows."""

from __future__ import annotations

from config import DolphinSchedulerSettings
from core.scheduler.domain import APPLICATION_START_PARAMETERS, ApplicationName
from core.scheduler.errors import DolphinSchedulerError


def ensure_application_project() -> None:
    """Create the DolphinScheduler user and project required by workflows."""
    DolphinSchedulerSettings.configure_sdk_environment()
    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.models.project import Project
        from pydolphinscheduler.models.user import User
    except (ImportError, OSError) as error:
        raise DolphinSchedulerError(str(error)) from error

    try:
        User(name=DolphinSchedulerSettings.USERNAME).create_if_not_exists()
        Project(name=DolphinSchedulerSettings.PROJECT_NAME).create_if_not_exists(
            DolphinSchedulerSettings.USERNAME
        )
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error


def submit_application_workflow(
    application: ApplicationName,
    *,
    task_group_id: int,
) -> int:
    """Submit one Runtime application workflow definition."""
    DolphinSchedulerSettings.configure_sdk_environment()
    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.tasks.shell import Shell
    except (ImportError, OSError) as error:
        raise DolphinSchedulerError(str(error)) from error

    try:
        workflow = Workflow(
            name=DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[application],
            description=f"Arena Runtime {application} 共享目录任务",
            user=DolphinSchedulerSettings.USERNAME,
            project=DolphinSchedulerSettings.PROJECT_NAME,
            worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            execution_type="PARALLEL",
            release_state="online",
            param={name: "" for name in APPLICATION_START_PARAMETERS},
        )
        with workflow:
            Shell(
                name=application,
                command=(
                    f"exec {DolphinSchedulerSettings.RUNTIME_COMMAND} "
                    f'apps {application} --input-file "${{input_file}}" '
                    '--output-dir "${output_dir}" '
                    '--output ${output} --cloud "${cloud}"'
                ),
                description=(
                    f"从共享目录 input.json 运行 {application}，"
                    "并将 Parquet 写回任务 output 目录"
                ),
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                task_group_id=task_group_id,
                task_group_priority=1,
            )
        return int(workflow.submit())
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error
