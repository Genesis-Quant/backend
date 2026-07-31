"""DolphinScheduler workflow, job, task, control, and log endpoints."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Response

from scheduler import (
    DolphinSchedulerError,
    JobAction,
    JobStateError,
    JobValidationError,
    SchedulerService,
    TaskAction,
    ensure_all_workflows,
    managed_workflow_definitions,
)

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


@router.get("/workflows")
def query_workflows() -> dict[str, Any]:
    return _call(managed_workflow_definitions)


@router.post("/workflows")
def define_workflows() -> dict[str, Any]:
    return _call(ensure_all_workflows)


@router.post("/incremental-updates")
def submit_incremental_update() -> dict[str, Any]:
    return _call(SchedulerService().submit_incremental_update)


@router.get("/jobs")
def query_jobs(
    application: str | None = None,
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    refresh: bool = False,
) -> dict[str, Any]:
    return _call(
        SchedulerService().list_jobs,
        application=application,
        state=state,
        limit=limit,
        refresh=refresh,
    )


@router.post("/jobs/query")
def submit_query_job(payload: dict[str, Any]) -> dict[str, Any]:
    return _submit_application_job("query", payload)


@router.post("/jobs/backtest")
def submit_backtest_job(payload: dict[str, Any]) -> dict[str, Any]:
    return _submit_application_job("backtest", payload)


@router.post("/jobs/factor")
def submit_factor_job(payload: dict[str, Any]) -> dict[str, Any]:
    return _submit_application_job("factor", payload)


@router.get("/jobs/{job_id}")
def query_job(
    job_id: str,
    include_tasks: bool = True,
) -> dict[str, Any]:
    return _call(
        SchedulerService().get_job,
        job_id,
        include_tasks=include_tasks,
    )


@router.get("/jobs/{job_id}/tasks")
def query_job_tasks(job_id: str) -> dict[str, Any]:
    return _call(SchedulerService().get_tasks, job_id)


@router.get("/jobs/{job_id}/tasks/{task_instance_id}")
def query_job_task(
    job_id: str,
    task_instance_id: int,
) -> dict[str, Any]:
    return _call(
        SchedulerService().get_task,
        job_id,
        task_instance_id,
    )


@router.get("/jobs/{job_id}/tasks/{task_instance_id}/logs")
def query_task_log(
    job_id: str,
    task_instance_id: int,
    skip_line_num: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=10000),
) -> dict[str, Any]:
    return _call(
        SchedulerService().get_task_log,
        job_id,
        task_instance_id,
        skip_line_num=skip_line_num,
        limit=limit,
    )


@router.get("/jobs/{job_id}/tasks/{task_instance_id}/logs/download")
def download_task_log(
    job_id: str,
    task_instance_id: int,
) -> Response:
    downloaded = _call(
        SchedulerService().download_task_log,
        job_id,
        task_instance_id,
    )
    return Response(
        content=downloaded.content,
        media_type=downloaded.content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{downloaded.filename}"'
            )
        },
    )


@router.post("/jobs/{job_id}/actions/{action}")
def control_job(job_id: str, action: JobAction) -> dict[str, Any]:
    return _call(SchedulerService().control_job, job_id, action)


@router.post("/jobs/{job_id}/tasks/{task_instance_id}/actions/{action}")
def control_task(
    job_id: str,
    task_instance_id: int,
    action: TaskAction,
) -> dict[str, Any]:
    return _call(
        SchedulerService().control_task,
        job_id,
        task_instance_id,
        action,
    )


@router.get("/audit-logs")
def query_audit_logs(
    page_no: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    model_types: str | None = None,
    operation_types: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    user_name: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    return _call(
        SchedulerService().audit_logs,
        page_no=page_no,
        page_size=page_size,
        model_types=model_types,
        operation_types=operation_types,
        start_date=start_date,
        end_date=end_date,
        user_name=user_name,
        model_name=model_name,
    )


@router.get("/audit-logs/types")
def query_audit_log_types() -> dict[str, Any]:
    return _call(SchedulerService().audit_log_types)


def _submit_application_job(
    application: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _call(
        SchedulerService().submit_application,
        application,
        payload,
    )


def _call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except (
        DolphinSchedulerError,
        FileNotFoundError,
        JobStateError,
        JobValidationError,
    ) as error:
        _raise_http(error)


def _raise_http(error: Exception) -> NoReturn:
    if isinstance(error, FileNotFoundError):
        status_code = 404
    elif isinstance(error, JobValidationError):
        status_code = 422
    elif isinstance(error, JobStateError):
        status_code = 409
    else:
        status_code = 502
    raise HTTPException(status_code=status_code, detail=str(error)) from error
