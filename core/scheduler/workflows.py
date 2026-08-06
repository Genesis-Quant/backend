"""Registration and discovery of application-managed workflows."""

from __future__ import annotations

from threading import RLock
from typing import Any

from config import DolphinSchedulerSettings
from core.scheduler.domain import ApplicationName
from core.scheduler.metadata import workflow_definition

WORKFLOW_LOCK = RLock()


def ensure_workflow_definition(
    workflow: ApplicationName,
) -> tuple[int, dict[str, Any]]:
    """Return one application definition loaded during backend startup."""
    name = DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES[workflow]
    return workflow_definition(name)
