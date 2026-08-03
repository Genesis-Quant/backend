"""Task Group provisioning for globally rate-limited Runtime tasks."""

from __future__ import annotations

from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.errors import DolphinSchedulerError

INCREMENTAL_TASK_GROUP_DESCRIPTION = (
    "Arena Tushare incremental update global concurrency limit"
)
INCREMENTAL_TASK_GROUP_SIZE = 1


def ensure_incremental_task_group() -> dict[str, Any]:
    """Create or update the incremental Task Group and return its record."""
    with DolphinSchedulerClient() as client:
        project_code = client.project_code(DolphinSchedulerSettings.PROJECT_NAME)
        if project_code is None:
            raise DolphinSchedulerError(f"创建 Task Group 前项目必须存在: {DolphinSchedulerSettings.PROJECT_NAME}")
        task_group = find_task_group(
            client,
            project_code=project_code,
            name=DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME,
        )
        if task_group is None:
            client.create_task_group(
                project_code=project_code,
                name=DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME,
                description=INCREMENTAL_TASK_GROUP_DESCRIPTION,
                group_size=INCREMENTAL_TASK_GROUP_SIZE,
            )
            task_group = find_task_group(
                client,
                project_code=project_code,
                name=DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME,
            )
        elif (
            int(task_group.get("groupSize", 0))
            != INCREMENTAL_TASK_GROUP_SIZE
            or task_group.get("description")
            != INCREMENTAL_TASK_GROUP_DESCRIPTION
        ):
            client.update_task_group(
                task_group_id=int(task_group["id"]),
                name=DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME,
                description=INCREMENTAL_TASK_GROUP_DESCRIPTION,
                group_size=INCREMENTAL_TASK_GROUP_SIZE,
            )
            task_group = find_task_group(
                client,
                project_code=project_code,
                name=DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME,
            )
    if task_group is None:
        raise DolphinSchedulerError(
            "Task Group 创建或更新后仍无法通过 API 查询: "
            f"{DolphinSchedulerSettings.INCREMENTAL_TASK_GROUP_NAME}"
        )
    return task_group


def find_task_group(
    client: DolphinSchedulerClient,
    *,
    project_code: int,
    name: str,
) -> dict[str, Any] | None:
    for page_no in range(1, 11):
        groups = client.task_groups(
            project_code=project_code,
            page_no=page_no,
            page_size=100,
        )
        for group in groups:
            if group.get("name") == name:
                return group
        if len(groups) < 100:
            break
    return None
