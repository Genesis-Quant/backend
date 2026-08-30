"""Authenticated DolphinScheduler task endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.apps.tasks.schemas import TaskAction, TaskActionResponse, TaskLogResponse, TaskLogScope
from core.apps.tasks.services import TaskGatewayService
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.http import raise_api_http_error

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("/{task_instance_id}/logs", response_model=TaskLogResponse)
def get_task_log(task_instance_id: int, workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], skip_line_num: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000), scope: TaskLogScope = Query(TaskLogScope.FULL), cursor: str | None = Query(default=None, max_length=512)) -> TaskLogResponse:
    try:
        return TaskLogResponse.model_validate(TaskGatewayService().log(session, user, workflow_instance_id, task_instance_id, skip_line_num, limit, scope, cursor))
    except (DolphinSchedulerError, FileNotFoundError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/{task_instance_id}/logs/download")
def download_task_log(task_instance_id: int, workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> StreamingResponse:
    try:
        log = TaskGatewayService().stream_log(session, user, workflow_instance_id, task_instance_id)
        return StreamingResponse(log.chunks(), media_type=log.content_type, headers={"Content-Disposition": f'attachment; filename="{log.filename}"'})
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_api_http_error(error)


@router.post("/{task_instance_id}/actions/{action}", response_model=TaskActionResponse)
def control_task(task_instance_id: int, workflow_instance_id: int, action: TaskAction, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> TaskActionResponse:
    try:
        return TaskActionResponse.model_validate(TaskGatewayService().control(session, user, workflow_instance_id, task_instance_id, action))
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)
