"""Backtest sensitivity-analysis workflow definition."""

from core.scheduler.applications.common import submit_application_workflow


def create_sensitivity_workflow(*, task_group_id: int) -> int:
    return submit_application_workflow(
        "sensitivity",
        task_group_id=task_group_id,
        include_output_argument=False,
    )
