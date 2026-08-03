"""Query projects and their reusable task records."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.apps.tasks.models import ApplicationTaskFields
from core.apps.tasks.models import utc_now
from core.database.base import Base


class QueryProject(Base):
    __tablename__ = "query_projects"
    __table_args__ = (Index("ix_query_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class QueryTask(ApplicationTaskFields, Base):
    __tablename__ = "query_tasks"
    __table_args__ = (
        Index(
            "uq_query_tasks_project_id",
            "project_id",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
            sqlite_where=text("project_id IS NOT NULL"),
        ),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("query_projects.id", ondelete="CASCADE"))
