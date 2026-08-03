"""Application runs and their DolphinScheduler workflow instances."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowRun(Base):
    """One application submission; it may create more than one workflow instance after reruns."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_user_created", "user_id", "created_at"),
        Index("ix_workflow_runs_application_submission", "application", "submission_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application: Mapped[str] = mapped_column(String(32), index=True)
    submission_state: Mapped[str] = mapped_column(String(64), default="CREATED", index=True)
    project_code: Mapped[int | None] = mapped_column(BigInteger)
    workflow_definition_code: Mapped[int | None] = mapped_column(BigInteger)
    workflow_name: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    requested_outputs: Mapped[list[str]] = mapped_column(JSON_VALUE)
    input_file: Mapped[str | None] = mapped_column(Text)
    output_dir: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __mapper_args__ = {
        "polymorphic_on": application,
        "polymorphic_abstract": True,
    }


class WorkflowInstance(Base):
    """A DolphinScheduler workflow (process) instance."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        Index(
            "uq_workflow_instances_current_run",
            "workflow_run_id",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_workflow_instances_state_started", "state", "started_at"),
    )

    workflow_instance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(64), index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    state_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
