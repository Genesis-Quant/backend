"""Query workflow definition."""

from core.scheduler.applications.common import submit_application_workflow


def create_query_workflow(*, task_group_id: int) -> int:
    return submit_application_workflow("query", task_group_id=task_group_id)
