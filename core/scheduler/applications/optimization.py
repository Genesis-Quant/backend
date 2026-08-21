"""Backtest parameter optimization workflow definition."""

from core.scheduler.applications.common import submit_application_workflow


def create_optimization_workflow(*, task_group_id: int) -> int:
    return submit_application_workflow(
        "optimization",
        task_group_id=task_group_id,
        include_output_argument=False,
    )
