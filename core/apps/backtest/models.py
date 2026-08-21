"""Backtest strategy projects, executions, and immutable versions."""

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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.apps.workflows.models import JSON_VALUE
from core.database.base import Base
from core.utils.time import utc_now


class BacktestProject(Base):
    __tablename__ = "backtest_projects"
    __table_args__ = (Index("ix_backtest_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, comment="策略回测项目主键")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), comment="所属用户主键")
    title: Mapped[str] = mapped_column(String(128), comment="项目名称")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")


class BacktestVersion(Base):
    __tablename__ = "backtest_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_backtest_versions_project_number"),
        UniqueConstraint("workflow_workspace_id", name="uq_backtest_versions_workflow_workspace"),
        UniqueConstraint("workflow_instance_id", name="uq_backtest_versions_workflow_instance"),
        CheckConstraint("NOT (saved AND is_current)", name="ck_backtest_versions_saved_not_current"),
        CheckConstraint("NOT saved OR workflow_instance_id IS NOT NULL", name="ck_backtest_versions_saved_workflow_instance"),
        Index(
            "uq_backtest_versions_current_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="回测版本主键")
    project_id: Mapped[int] = mapped_column(ForeignKey("backtest_projects.id", ondelete="CASCADE"), comment="所属回测项目主键")
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="RESTRICT"),
        comment="版本独占的回测工作空间主键",
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
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, comment="当前版本的回测请求参数")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, comment="已保存版本的回测摘要指标")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")


class BacktestOptimization(Base):
    __tablename__ = "backtest_optimizations"
    __table_args__ = (
        Index("ix_backtest_optimizations_version_created", "version_id", "created_at"),
        UniqueConstraint("workflow_workspace_id", name="uq_backtest_optimizations_workflow_workspace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="参数调优报告主键")
    version_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_versions.id", ondelete="CASCADE"),
        comment="来源回测版本主键",
    )
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="RESTRICT"),
        comment="参数调优报告独占的工作流工作空间主键",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")


class BacktestResearch(Base):
    __tablename__ = "backtest_researches"
    __table_args__ = (
        Index("ix_backtest_researches_version_type_created", "version_id", "analysis_type", "created_at"),
        UniqueConstraint("workflow_workspace_id", name="uq_backtest_researches_workflow_workspace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="批量研究主键")
    version_id: Mapped[int] = mapped_column(ForeignKey("backtest_versions.id", ondelete="CASCADE"), comment="来源回测版本主键")
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="RESTRICT"),
        comment="敏感性分析独占的工作流工作空间主键",
    )
    analysis_type: Mapped[str] = mapped_column(String(64), comment="批量研究类型")
    description: Mapped[str] = mapped_column(String(512), default="", comment="批量研究备注")
    result_workflow_instance_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workflow_instances.workflow_instance_id", ondelete="SET NULL"),
        comment="已完成结果汇总对应的工作流实例主键",
    )
    completed_count: Mapped[int | None] = mapped_column(
        Integer,
        comment="结果文件中执行成功的参数组合数量",
    )
    failed_count: Mapped[int | None] = mapped_column(
        Integer,
        comment="结果文件中执行失败的参数组合数量",
    )
    result_error: Mapped[str | None] = mapped_column(
        Text,
        comment="结果文件收集或校验错误",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
