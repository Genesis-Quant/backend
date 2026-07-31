"""Incremental data update workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scheduler.config import DolphinSchedulerSettings

INCREMENTAL_WORKERS = (
    "daily",
    "fund-daily",
    "fund-adj-factor",
    "limit",
    "daily-basic",
    "adj-factor",
    "hfq",
    "st",
    "balance-sheet",
    "income",
    "cashflow",
    "fina-indicator",
    "dividend",
    "index-weight",
)


class DolphinSchedulerError(RuntimeError):
    pass


def create_and_submit_incremental_update(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, int]:
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    current_settings.configure_sdk_environment()
    run_time = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None) + timedelta(seconds=30)

    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.tasks.shell import Shell

        workflow = Workflow(
            name=current_settings.workflow_name,
            description="Arena Runtime 全量增量更新任务",
            user=current_settings.username,
            project=current_settings.project_name,
            worker_group=current_settings.worker_group,
            execution_type="PARALLEL",
            release_state="online",
            schedule=(
                f"{run_time.second} {run_time.minute} {run_time.hour} "
                f"{run_time.day} {run_time.month} ? {run_time.year}"
            ),
            start_time=run_time - timedelta(seconds=5),
            end_time=run_time + timedelta(seconds=5),
            online_schedule=True,
        )
        with workflow:
            previous = None
            for worker_name in INCREMENTAL_WORKERS:
                task = Shell(
                    name=worker_name,
                    command=(
                        f"exec {current_settings.runtime_command} workers {worker_name} "
                        f"--threads {current_settings.incremental_threads} "
                        f"--throttle {current_settings.incremental_throttle}"
                    ),
                    description=f"{worker_name} 增量更新",
                    worker_group=current_settings.worker_group,
                    fail_retry_times=1,
                    fail_retry_interval=1,
                )
                if previous is not None:
                    previous >> task
                previous = task
        workflow_code = int(workflow.submit())
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error
    return {"workflow_code": workflow_code, "task_count": len(INCREMENTAL_WORKERS)}
