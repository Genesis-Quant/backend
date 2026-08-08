"""Backtest strategy projects, executions, and immutable versions."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.apps.workflows.models import JSON_VALUE, WorkflowRun
from core.database.base import Base
from core.utils.time import utc_now


class BacktestProject(Base):
    __tablename__ = "backtest_projects"
    __table_args__ = (Index("ix_backtest_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class BacktestWorkflowRun(WorkflowRun):
    __tablename__ = "backtest_workflow_runs"
    __table_args__ = (
        Index(
            "uq_backtest_workflow_runs_project_draft",
            "project_id",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL AND saved = false"),
            sqlite_where=text("project_id IS NOT NULL AND saved = 0"),
        ),
    )

    id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_projects.id", ondelete="SET NULL"), index=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __mapper_args__ = {"polymorphic_identity": "backtest"}


class BacktestVersion(Base):
    __tablename__ = "backtest_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_backtest_versions_project_number"),
        UniqueConstraint("workflow_instance_id", name="uq_backtest_versions_workflow_instance"),
        Index("ix_backtest_versions_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("backtest_projects.id", ondelete="CASCADE"))
    workflow_instance_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workflow_instances.workflow_instance_id", ondelete="RESTRICT"),
    )
    version: Mapped[int] = mapped_column(Integer)
    remark: Mapped[str] = mapped_column(String(512), default="")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BacktestResearch(Base):
    __tablename__ = "backtest_researches"
    __table_args__ = (Index("ix_backtest_researches_version_type_created", "version_id", "analysis_type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("backtest_versions.id", ondelete="CASCADE"))
    analysis_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    runs: Mapped[list["BacktestResearchRun"]] = relationship(back_populates="research", cascade="all, delete-orphan", order_by="BacktestResearchRun.id")


class BacktestResearchRun(Base):
    __tablename__ = "backtest_research_runs"
    __table_args__ = (UniqueConstraint("workflow_run_id", name="uq_backtest_research_runs_workflow_run"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    research_id: Mapped[int] = mapped_column(ForeignKey("backtest_researches.id", ondelete="CASCADE"))
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    parameter_overrides: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    research: Mapped[BacktestResearch] = relationship(back_populates="runs")
