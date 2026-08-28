"""SQLAlchemy models for Arena users."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base
from core.utils.time import utc_now


class User(Base):
    """Registered Arena user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, comment="用户主键")
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="登录用户名")
    password_hash: Mapped[str] = mapped_column(String(255), comment="密码哈希")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否为管理员")
    mcp_custom_prompt: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
        comment="注入 Arena MCP 总览的用户自定义提示词",
    )
    mcp_allow_delete_query_projects: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除数据查询项目",
    )
    mcp_allow_delete_factor_projects: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除因子分析项目",
    )
    mcp_allow_delete_backtest_projects: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除策略回测项目",
    )
    mcp_allow_delete_factor_versions: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除因子分析版本",
    )
    mcp_allow_delete_backtest_versions: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除策略回测版本",
    )
    mcp_allow_delete_fee_analyses: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除手续费分析",
    )
    mcp_allow_delete_sensitivity_analyses: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除参数敏感性分析",
    )
    mcp_allow_delete_optimizations: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        comment="是否允许 MCP 删除参数调优报告",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")
