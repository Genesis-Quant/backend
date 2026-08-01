"""Backtest task table."""

from config.database import Base
from apps.tasks.models import ApplicationTaskFields


class BacktestTask(ApplicationTaskFields, Base):
    __tablename__ = "backtest_tasks"
