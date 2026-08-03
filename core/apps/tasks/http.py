"""HTTP error mapping shared by task and result endpoints."""

from typing import NoReturn

from fastapi import HTTPException

from core.scheduler.errors import DolphinSchedulerError


def raise_task_http_error(error: Exception) -> NoReturn:
    if isinstance(error, FileNotFoundError):
        status_code = 404
    elif isinstance(error, ValueError):
        status_code = 422
    elif isinstance(error, RuntimeError) and not isinstance(error, DolphinSchedulerError):
        status_code = 409
    else:
        status_code = 502
    raise HTTPException(status_code=status_code, detail=str(error)) from error
