"""SQLAlchemy model for authenticated user feedback."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base
from core.utils.time import utc_now


class Feedback(Base):
    """Feedback submitted from the Arena web application or MCP server."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("source IN ('web', 'mcp')", name="ck_feedback_source"),
        CheckConstraint(
            "length(content) BETWEEN 1 AND 4000",
            name="ck_feedback_content_length",
        ),
        Index("ix_feedback_user_created", "user_id", "created_at"),
        {"comment": "用户通过网页或 MCP 提交的反馈"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="反馈主键")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        comment="提交反馈的用户主键",
    )
    source: Mapped[str] = mapped_column(String(16), comment="反馈来源：web 或 mcp")
    content: Mapped[str] = mapped_column(Text, comment="反馈正文")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        comment="提交时间",
    )
