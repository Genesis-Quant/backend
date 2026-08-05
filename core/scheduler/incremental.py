"""Incremental data update workflow definition."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from runtime.messaging.channels import normalize_channel_name
from runtime.workers.registry import (
    WORKER_DESCRIPTIONS,
    WORKER_ORDER,
    normalize_worker_names,
)

from config import DolphinSchedulerSettings
from core.scheduler.domain import INCREMENTAL_START_PARAMETERS
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.task_groups import ensure_incremental_task_group

INCREMENTAL_WORKERS = WORKER_ORDER
INCREMENTAL_CONTROL_TASK_COUNT = 2
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
        from pydolphinscheduler.tasks.python import Python
        from pydolphinscheduler.tasks.shell import Shell

        workflow = Workflow(
            name=DolphinSchedulerSettings.WORKFLOW_NAME,
            description="Arena Runtime 全量增量更新任务",
            user=DolphinSchedulerSettings.USERNAME,
            project=DolphinSchedulerSettings.PROJECT_NAME,
            worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            execution_type="PARALLEL",
            release_state="online",
            param={name: "" for name in INCREMENTAL_START_PARAMETERS},
        )
        with workflow:
            worker_tasks = []
            for index, worker_name in enumerate(INCREMENTAL_WORKERS):
                worker_tasks.append(
                    Shell(
                        name=worker_name,
                        command=(
                            f"exec {DolphinSchedulerSettings.RUNTIME_COMMAND} "
                            f"workers {worker_name} "
                            '--job-id "${job_id}" '
                            '--output-dir "${output_dir}" '
                            '--selected-workers "${workers}"'
                        ),
                        description=f"{worker_name} 增量更新",
                        worker_group=DolphinSchedulerSettings.WORKER_GROUP,
                        fail_retry_times=1,
                        fail_retry_interval=1,
                        task_group_id=task_group_id,
                        task_group_priority=len(INCREMENTAL_WORKERS) - index,
                    )
                )

            message_task = Python(
                name="send-incremental-update-message",
                definition=incremental_message_task_definition(),
                description="汇总 Worker 结果并发送增量更新消息",
                worker_group=DolphinSchedulerSettings.WORKER_GROUP,
            )
            Condition(
                name="check-incremental-update-result",
                condition=And(And(SUCCESS(*worker_tasks))),
                success_task=message_task,
                failed_task=message_task,
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


def incremental_message_task_definition() -> str:
    """Return the Python node that summarizes Worker output and sends it."""
    runtime_python = str(
        PurePosixPath(DolphinSchedulerSettings.RUNTIME_COMMAND).with_name("python")
    )
    return f'''
import os
import sys
from pathlib import Path

runtime_python = {runtime_python!r}
if os.path.realpath(sys.executable) != os.path.realpath(runtime_python):
    os.execv(runtime_python, [runtime_python, *sys.argv])

from runtime.messaging import send_message, write_message
from runtime.workers.registry import normalize_worker_names
from runtime.workers.report import build_incremental_message

output_dir = Path("${{output_dir}}").expanduser().resolve()
workers = normalize_worker_names(
    tuple(
        name.strip()
        for name in "${{workers}}".split(",")
        if name.strip()
    )
)
message = build_incremental_message(
    str(output_dir),
    job_id="${{job_id}}",
    selected_workers=workers,
)
write_message(output_dir / "message.json", message)
delivery = send_message(message, "${{channel}}")
print(delivery.model_dump_json())
'''.strip()


def normalize_incremental_workers(
    workers: Sequence[str] | None,
) -> tuple[str, ...]:
    """返回一次增量更新实际选择的规范 Worker 名称。"""
    if workers is None:
        return INCREMENTAL_WORKERS
    return normalize_worker_names(workers)


def incremental_worker_options() -> list[dict[str, str]]:
    """返回管理端可选择的增量 Worker 及其说明。"""
    return [
        {"name": name, "description": WORKER_DESCRIPTIONS[name]}
        for name in INCREMENTAL_WORKERS
    ]


def normalize_incremental_channel(channel: str | None) -> str:
    """返回增量工作流使用的规范消息 Channel。"""
    return normalize_channel_name(channel)
