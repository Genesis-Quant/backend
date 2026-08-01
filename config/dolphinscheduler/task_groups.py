"""Task Group provisioning for globally rate-limited Runtime tasks."""

from __future__ import annotations

from typing import Any

from config.dolphinscheduler.client import DolphinSchedulerClient
from config.dolphinscheduler.errors import DolphinSchedulerError
from config.settings import DolphinSchedulerSettings

INCREMENTAL_TASK_GROUP_DESCRIPTION = (
    "Arena Tushare incremental update global concurrency limit"
)


def ensure_incremental_task_group(
    settings: DolphinSchedulerSettings,
) -> dict[str, Any]:
    """Create or update the incremental Task Group and return its record."""
    with DolphinSchedulerClient(settings) as client:
        project_code = client.project_code(settings.project_name)
        if project_code is None:
            raise DolphinSchedulerError(
                f"创建 Task Group 前项目必须存在: {settings.project_name}"
            )
        task_group = find_task_group(
            client,
            project_code=project_code,
            name=settings.incremental_task_group_name,
        )
        if task_group is None:
            client.create_task_group(
                project_code=project_code,
                name=settings.incremental_task_group_name,
                description=INCREMENTAL_TASK_GROUP_DESCRIPTION,
                group_size=settings.incremental_task_group_size,
            )
            task_group = find_task_group(
                client,
                project_code=project_code,
                name=settings.incremental_task_group_name,
            )
        elif (
            int(task_group.get("groupSize", 0))
            != settings.incremental_task_group_size
            or task_group.get("description")
            != INCREMENTAL_TASK_GROUP_DESCRIPTION
        ):
            client.update_task_group(
                task_group_id=int(task_group["id"]),
                name=settings.incremental_task_group_name,
                description=INCREMENTAL_TASK_GROUP_DESCRIPTION,
                group_size=settings.incremental_task_group_size,
            )
            task_group = find_task_group(
                client,
                project_code=project_code,
                name=settings.incremental_task_group_name,
            )
    if task_group is None:
        raise DolphinSchedulerError(
            "Task Group 创建或更新后仍无法通过 API 查询: "
            f"{settings.incremental_task_group_name}"
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
