"""Incremental data update workflow definition."""

from __future__ import annotations

from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.task_groups import ensure_incremental_task_group

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
INCREMENTAL_CONTROL_TASK_COUNT = 3
INCREMENTAL_TASK_COUNT = len(INCREMENTAL_WORKERS) + INCREMENTAL_CONTROL_TASK_COUNT


def create_incremental_update_workflow() -> dict[str, Any]:
    """Create or update the incremental workflow without executing it."""
    task_group = ensure_incremental_task_group()
    workflow_code = submit_incremental_update_workflow(task_group_id=int(task_group["id"]))
    return {
        "name": DolphinSchedulerSettings.WORKFLOW_NAME,
        "workflow_code": workflow_code,
        "task_group": {
            "id": int(task_group["id"]),
            "name": task_group["name"],
            "group_size": int(task_group["groupSize"]),
        },
        **incremental_task_counts(),
    }


def submit_incremental_update_workflow(
    *,
    task_group_id: int,
) -> int:
    DolphinSchedulerSettings.configure_sdk_environment()
    try:
        from py4j.protocol import Py4JError
        from pydolphinscheduler.core.workflow import Workflow
        from pydolphinscheduler.exceptions import PyDSBaseException
        from pydolphinscheduler.tasks.condition import SUCCESS, And, Condition
        from pydolphinscheduler.tasks.shell import Shell

        workflow = Workflow(
            name=DolphinSchedulerSettings.WORKFLOW_NAME,
            description="Arena Runtime 全量增量更新任务",
            user=DolphinSchedulerSettings.USERNAME,
            project=DolphinSchedulerSettings.PROJECT_NAME,
            worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            execution_type="PARALLEL",
            release_state="online",
            param={"job_id": "definition-default"},
        )
        with workflow:
            worker_tasks = []
            for index, worker_name in enumerate(INCREMENTAL_WORKERS):
                worker_tasks.append(
                    Shell(
                        name=worker_name,
                        command=(
                            f"exec {DolphinSchedulerSettings.RUNTIME_COMMAND} workers {worker_name}"
                        ),
                        description=f"{worker_name} 增量更新",
                        worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                        fail_retry_times=1,
                        fail_retry_interval=1,
                        task_group_id=task_group_id,
                        task_group_priority=len(INCREMENTAL_WORKERS) - index,
                    )
                )

            success_task = Shell(
                name="incremental-update-succeeded",
                command='echo "All incremental update tasks succeeded"',
                description="全部增量更新任务成功",
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            )
            failure_task = Shell(
                name="incremental-update-failed",
                command=(
                    'echo "One or more incremental update tasks failed" >&2; exit 1'
                ),
                description="存在失败的增量更新任务",
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            )
            Condition(
                name="check-incremental-update-result",
                condition=And(And(SUCCESS(*worker_tasks))),
                success_task=success_task,
                failed_task=failure_task,
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            )
        workflow_code = int(workflow.submit())
    except (OSError, Py4JError, PyDSBaseException) as error:
        raise DolphinSchedulerError(str(error)) from error
    return workflow_code


def incremental_task_counts() -> dict[str, int]:
    return {
        "worker_task_count": len(INCREMENTAL_WORKERS),
        "control_task_count": INCREMENTAL_CONTROL_TASK_COUNT,
        "task_count": INCREMENTAL_TASK_COUNT,
    }
