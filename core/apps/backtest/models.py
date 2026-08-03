"""Backtest strategy projects, executions, and immutable versions."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base
from core.apps.tasks.models import ApplicationTaskFields, JSON_VALUE, utc_now


class BacktestProject(Base):
    __tablename__ = "backtest_projects"
    __table_args__ = (Index("ix_backtest_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class BacktestTask(ApplicationTaskFields, Base):
    __tablename__ = "backtest_tasks"
    __table_args__ = (
        Index(
            "uq_backtest_tasks_project_draft",
            "project_id",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL AND saved = false"),
            sqlite_where=text("project_id IS NOT NULL AND saved = 0"),
        ),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_projects.id", ondelete="CASCADE"), index=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class BacktestVersion(Base):
    __tablename__ = "backtest_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_backtest_versions_project_number"),
        UniqueConstraint("task_record_id", name="uq_backtest_versions_task_record"),
        Index("ix_backtest_versions_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("backtest_projects.id", ondelete="CASCADE"))
    task_record_id: Mapped[int] = mapped_column(ForeignKey("backtest_tasks.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    remark: Mapped[str] = mapped_column(String(512), default="")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
