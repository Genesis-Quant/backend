"""Database fields shared by application task tables."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class ApplicationTaskFields:
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    project_code: Mapped[int | None] = mapped_column(BigInteger)
    process_definition_code: Mapped[int | None] = mapped_column(BigInteger)
    process_instance_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    workflow_name: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(64), default="CREATED", index=True)
    process_state: Mapped[str | None] = mapped_column(String(64))
    host: Mapped[str | None] = mapped_column(String(255))
    retry_times: Mapped[int | None] = mapped_column(Integer)
    max_retry_times: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    requested_outputs: Mapped[list[str]] = mapped_column(JSON_VALUE)
    task_id_history: Mapped[list[int]] = mapped_column(JSON_VALUE, default=list)
    process_instance_history: Mapped[list[int]] = mapped_column(JSON_VALUE, default=list)
    workflow_tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    state_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    input_file: Mapped[str | None] = mapped_column(Text)
    output_dir: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class WorkflowTaskInstance(Base):
    """Indexed ownership mapping for DolphinScheduler child task instances."""

    __tablename__ = "workflow_task_instances"
    __table_args__ = (
        Index(
            "ix_workflow_task_instances_parent",
            "application",
            "record_id",
        ),
    )

    task_instance_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    application: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[int] = mapped_column(Integer)
    task_code: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
