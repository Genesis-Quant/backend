"""Administrator-owned workflow models."""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base


class IncrementalWorkflowWorkspace(Base):
    __tablename__ = "incremental_workflow_workspaces"

    id: Mapped[int] = mapped_column(ForeignKey("workflow_workspaces.id", ondelete="CASCADE"), primary_key=True, comment="工作流工作空间主键")
