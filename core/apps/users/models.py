"""SQLAlchemy models for Arena users."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")
