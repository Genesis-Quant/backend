"""Factor analysis task table."""

from config.database import Base
from apps.tasks.models import ApplicationTaskFields


class FactorTask(ApplicationTaskFields, Base):
    __tablename__ = "factor_tasks"
