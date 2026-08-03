"""Authenticated DolphinScheduler task gateway endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.apps.tasks.http import raise_task_http_error
from core.apps.tasks.schemas import TaskAction, TaskActionResponse, TaskDeletedResponse, TaskListResponse, TaskLogResponse, TaskStatusResponse
from core.apps.tasks.services import TaskGatewayService
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("", response_model=TaskListResponse)
def list_tasks(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), application: Literal["query", "factor", "backtest"] | None = None, state: Literal["active", "success", "failure"] | None = None) -> TaskListResponse:
    return TaskGatewayService().list(session, user.id, page, page_size, application, state)


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> TaskStatusResponse:
    try:
        return TaskGatewayService().status(session, user.id, task_id)
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_task_http_error(error)


@router.get("/{task_id}/logs", response_model=TaskLogResponse)
def get_task_log(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], skip_line_num: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000)) -> TaskLogResponse:
    try:
        return TaskGatewayService().log(session, user.id, task_id, skip_line_num, limit)
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_task_http_error(error)


@router.get("/{task_id}/logs/download")
def download_task_log(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> StreamingResponse:
    try:
        log = TaskGatewayService().stream_log(session, user.id, task_id)
        return StreamingResponse(log.chunks(), media_type=log.content_type, headers={"Content-Disposition": f'attachment; filename="{log.filename}"'})
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_task_http_error(error)


@router.post("/{task_id}/actions/{action}", response_model=TaskActionResponse)
def control_task(task_id: int, action: TaskAction, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> TaskActionResponse:
    try:
        return TaskGatewayService().control(session, user.id, task_id, action)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError) as error:
        raise_task_http_error(error)


@router.delete("/{task_id}", response_model=TaskDeletedResponse)
def delete_task(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> TaskDeletedResponse:
    try:
        return TaskGatewayService().delete(session, user.id, task_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)
