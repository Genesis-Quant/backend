"""Shared API exception-to-HTTP mapping."""

from typing import NoReturn

from fastapi import HTTPException

from core.scheduler.errors import DolphinSchedulerError


def raise_api_http_error(error: Exception) -> NoReturn:
    if isinstance(error, FileNotFoundError):
        status_code = 404
    elif isinstance(error, ValueError):
        status_code = 422
    elif isinstance(error, RuntimeError) and not isinstance(error, DolphinSchedulerError):
        status_code = 409
    else:
        status_code = 502
    api_code = getattr(error, "api_code", None)
    detail: str | dict[str, str] = str(error)
    if isinstance(api_code, str):
        detail = {"code": api_code, "message": str(error)}
    raise HTTPException(status_code=status_code, detail=detail) from error
