"""Persistent records for incremental-update workflow executions."""

from core.apps.tasks.models import ApplicationTaskFields
from core.database.base import Base


class IncrementalUpdateTask(ApplicationTaskFields, Base):
    __tablename__ = "incremental_update_tasks"
