"""Query submission and completed result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from apps.query.schemas import QueryResultFile, QueryTaskCreate, QueryTaskSubmitted
from apps.query.services import query_result_files, query_result_path, submit_query_task
from apps.tasks.http import raise_task_http_error
from apps.users.models import User
from apps.users.services import get_current_user
from config.database import get_database_session
from config.dolphinscheduler.errors import DolphinSchedulerError

router = APIRouter(prefix="/api/v1/query/tasks", tags=["query"])


@router.post("", response_model=QueryTaskSubmitted, status_code=status.HTTP_202_ACCEPTED)
def create_query_task(request: QueryTaskCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryTaskSubmitted:
    try:
        task = submit_query_task(session, user.id, request.model_dump(exclude={"output"}, mode="json"), list(request.output))
        return QueryTaskSubmitted(task_id=int(task.task_id))
    except (DolphinSchedulerError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/{task_id}/outputs", response_model=list[QueryResultFile])
def list_query_results(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[QueryResultFile]:
    try:
        return query_result_files(session, user.id, task_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.get("/{task_id}/outputs/{name}", response_class=FileResponse)
def download_query_result(task_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FileResponse:
    try:
        path = query_result_path(session, user.id, task_id, name)
        return FileResponse(path, filename=path.name, media_type="application/vnd.apache.parquet")
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)
