"""SQLAlchemy model metadata for Backend-managed tables."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.database import BACKEND_SCHEMA


class Base(DeclarativeBase):
    """Base class for tables managed by Backend Alembic migrations."""

    metadata = MetaData(schema=BACKEND_SCHEMA)
