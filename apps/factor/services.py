"""Factor task submission and result access."""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from apps.factor.models import FactorTask
from apps.tasks.services import TaskExecutionService
from apps.utils.results import result_files, result_path

OUTPUT_FILES = {
    "processed_data": "factor_processed.parquet",
    "information_coefficient": "factor_information_coefficients.parquet",
    "group_returns": "factor_group_returns.parquet",
}


def submit_factor_task(session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> FactorTask:
    return TaskExecutionService("factor", FactorTask).submit(session, user_id, payload, outputs)


def factor_result_files(session: Session, user_id: int, task_id: int) -> list[dict[str, Any]]:
    return result_files(session, user_id, task_id, FactorTask, OUTPUT_FILES)


def factor_result_path(session: Session, user_id: int, task_id: int, name: str) -> Path:
    return result_path(session, user_id, task_id, name, FactorTask, OUTPUT_FILES)
