"""Application workspaces, submission attempts, and scheduler instances."""

from datetime import datetime
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

from core.apps.workflows.artifacts import new_workspace_key
from core.database.base import Base
from core.utils.time import utc_now

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class WorkflowWorkspace(Base):
    """One logical application workspace shared by all of its attempts."""

    __tablename__ = "workflow_workspaces"
    __table_args__ = (
        Index("ix_workflow_workspaces_user_created", "user_id", "created_at"),
        Index("ix_workflow_workspaces_application_created", "application", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="工作流工作空间主键")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), comment="所属用户主键")
    application: Mapped[str] = mapped_column(String(32), comment="所属应用类型")
    workspace_key: Mapped[str] = mapped_column(
        String(32),
        default=new_workspace_key,
        index=True,
        unique=True,
        comment="共享文件工作空间唯一标识",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")


class WorkflowAttempt(Base):
    """One submission attempt inside a logical workspace."""

    __tablename__ = "workflow_attempts"
    __table_args__ = (
        Index(
            "uq_workflow_attempts_current_workspace",
            "workflow_workspace_id",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_workflow_attempts_submission_id", "submission_state", "id"),
        Index("ix_workflow_attempts_workspace_created", "workflow_workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="工作流提交尝试主键")
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="CASCADE"),
        comment="所属工作流工作空间主键",
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否为工作空间当前提交尝试")
    submission_state: Mapped[str] = mapped_column(String(64), default="CREATED", comment="提交尝试状态")
    project_code: Mapped[int | None] = mapped_column(BigInteger, comment="DolphinScheduler 项目编码")
    workflow_definition_code: Mapped[int | None] = mapped_column(BigInteger, comment="DolphinScheduler 工作流定义编码")
    workflow_name: Mapped[str | None] = mapped_column(String(128), comment="工作流定义名称")
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, comment="提交给应用的请求参数")
    start_parameters: Mapped[dict[str, str]] = mapped_column(JSON_VALUE, default=dict, comment="提交给调度器的启动参数")
    requested_outputs: Mapped[list[str]] = mapped_column(JSON_VALUE, comment="请求生成的输出名称")
    error: Mapped[str | None] = mapped_column(Text, comment="准备或提交错误信息")
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list, comment="提交尝试业务事件记录")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")


class WorkflowInstance(Base):
    """A DolphinScheduler workflow (process) instance."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        Index("ix_workflow_instances_state_started", "state", "started_at"),
        Index("ix_workflow_instances_created_id", "created_at", "workflow_instance_id"),
    )

    workflow_instance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, comment="DolphinScheduler 工作流实例主键")
    workflow_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_attempts.id", ondelete="CASCADE"),
        unique=True,
        comment="所属工作流提交尝试主键",
    )
    state: Mapped[str] = mapped_column(String(64), comment="工作流实例状态")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="开始执行时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="结束执行时间")
    duration_seconds: Mapped[float | None] = mapped_column(Float, comment="执行耗时秒数")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最近同步调度器时间")
    error: Mapped[str | None] = mapped_column(Text, comment="实例执行错误信息")
    state_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list, comment="实例状态变更历史")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")
