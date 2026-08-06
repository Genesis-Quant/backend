"""DolphinScheduler workflow definitions grouped by application."""

from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.applications.backtest import create_backtest_workflow
from core.scheduler.applications.common import ensure_application_project
from core.scheduler.applications.factor import create_factor_workflow
from core.scheduler.applications.query import create_query_workflow
from core.scheduler.domain import APPLICATIONS
from core.scheduler.task_groups import ensure_application_task_groups


def create_application_workflows() -> dict[str, Any]:
    """Create or update the query, factor, and backtest workflows."""
    ensure_application_project()
    task_groups = ensure_application_task_groups()
    workflow_codes = {
        "query": create_query_workflow(
            task_group_id=int(task_groups["query"]["id"])
        ),
        "factor": create_factor_workflow(
            task_group_id=int(task_groups["factor"]["id"])
        ),
        "backtest": create_backtest_workflow(
            task_group_id=int(task_groups["backtest"]["id"])
        ),
    }
    return {
        "project_name": DolphinSchedulerSettings.PROJECT_NAME,
        "workflows": {
            application: {
                "name": DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[application],
                "code": workflow_codes[application],
                "task_group": {
                    "id": int(task_groups[application]["id"]),
                    "name": task_groups[application]["name"],
                    "group_size": int(task_groups[application]["groupSize"]),
                },
            }
            for application in APPLICATIONS
        },
    }
