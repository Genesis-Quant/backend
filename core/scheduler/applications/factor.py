"""Factor-analysis workflow definition."""

from core.scheduler.applications.common import submit_application_workflow


def create_factor_workflow(*, task_group_id: int) -> int:
    return submit_application_workflow("factor", task_group_id=task_group_id)
