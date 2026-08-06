"""Query projects and their current workflow runs."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from core.apps.workflows.models import WorkflowRun
from core.database.base import Base
from core.utils.time import utc_now


class QueryProject(Base):
    __tablename__ = "query_projects"
    __table_args__ = (Index("ix_query_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class QueryWorkflowRun(WorkflowRun):
    __tablename__ = "query_workflow_runs"

    id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("query_projects.id", ondelete="SET NULL"), unique=True)

    __mapper_args__ = {"polymorphic_identity": "query"}
