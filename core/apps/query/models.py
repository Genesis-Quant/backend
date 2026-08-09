"""Query projects and their reusable workflow workspaces."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base
from core.utils.time import utc_now


class QueryProject(Base):
    __tablename__ = "query_projects"
    __table_args__ = (Index("ix_query_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, comment="查询项目主键")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), comment="所属用户主键")
    workflow_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_workspaces.id", ondelete="RESTRICT"),
        unique=True,
        comment="查询项目独占的工作流工作空间主键",
    )
    title: Mapped[str] = mapped_column(String(128), comment="项目名称")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")
