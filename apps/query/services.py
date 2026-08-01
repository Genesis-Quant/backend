"""Query task submission and result access."""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from apps.query.models import QueryTask
from apps.tasks.services import TaskExecutionService
from apps.utils.results import result_files, result_path

OUTPUT_FILES = {
    "source_data": "source_data.parquet",
    "computed_data": "computed_data.parquet",
    "filtered_data": "filtered_data.parquet",
    "data": "query.parquet",
}


def submit_query_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> QueryTask:
    return TaskExecutionService("query", QueryTask).submit(session, user_id, payload, outputs)


def query_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, QueryTask, OUTPUT_FILES)


def query_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, QueryTask, OUTPUT_FILES)
