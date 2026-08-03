"""Incremental-update workflow submissions."""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from core.apps.workflows.models import WorkflowRun


class IncrementalWorkflowRun(WorkflowRun):
    __tablename__ = "incremental_workflow_runs"

    id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "incremental"}
