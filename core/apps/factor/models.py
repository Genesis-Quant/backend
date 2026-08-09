"""Factor research projects, executions, and immutable versions."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.apps.workflows.models import JSON_VALUE
from core.database.base import Base
from core.utils.time import utc_now


class FactorProject(Base):
    __tablename__ = "factor_projects"
    __table_args__ = (Index("ix_factor_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, comment="因子研究项目主键")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), comment="所属用户主键")
    title: Mapped[str] = mapped_column(String(128), comment="项目名称")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")


class FactorVersion(Base):
    __tablename__ = "factor_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_factor_versions_project_number"),
        UniqueConstraint("workflow_workspace_id", name="uq_factor_versions_workflow_workspace"),
        UniqueConstraint("workflow_instance_id", name="uq_factor_versions_workflow_instance"),
        CheckConstraint("NOT (saved AND is_current)", name="ck_factor_versions_saved_not_current"),
        CheckConstraint("NOT saved OR workflow_instance_id IS NOT NULL", name="ck_factor_versions_saved_workflow_instance"),
        Index(
            "uq_factor_versions_current_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="因子版本主键")
    project_id: Mapped[int] = mapped_column(ForeignKey("factor_projects.id", ondelete="CASCADE"), comment="所属因子项目主键")
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="RESTRICT"),
        comment="版本独占的因子工作空间主键",
    )
    workflow_instance_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workflow_instances.workflow_instance_id", ondelete="RESTRICT"),
        comment="已保存结果采用的工作流实例主键",
    )
    version: Mapped[int] = mapped_column(Integer, comment="项目内递增版本号")
    saved: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已经保存为不可变版本")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为项目当前可更新版本")
    remark: Mapped[str] = mapped_column(String(512), default="", comment="版本备注")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, comment="当前版本的因子分析请求参数")
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, comment="已保存版本的因子分析摘要指标")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")
