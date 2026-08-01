"""Query task table."""

from config.database import Base
from apps.tasks.models import ApplicationTaskFields


class QueryTask(ApplicationTaskFields, Base):
    __tablename__ = "query_tasks"
