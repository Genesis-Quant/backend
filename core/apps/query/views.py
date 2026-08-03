"""Query projects, submissions, DSL catalog, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from runtime.apps.query.schema import FactorQuery
from sqlalchemy.orm import Session

from core.apps.query.schemas import DslCatalog, QueryProjectCreate, QueryProjectItem, QueryProjectPage, QueryProjectSubmitted, QueryResultFile, QueryTaskCreate, QueryTaskSubmitted
from core.apps.query.services import create_query_project, delete_query_project, get_query_project, list_query_projects, query_dsl_catalog, query_result_files, query_result_path, submit_project_query, submit_query_task
from core.apps.tasks.http import raise_task_http_error
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError

router = APIRouter(prefix="/api/v1/query", tags=["query"])


@router.post("/tasks", response_model=QueryTaskSubmitted, status_code=status.HTTP_202_ACCEPTED)
def create_query_task(request: QueryTaskCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryTaskSubmitted:
    try:
        task = submit_query_task(session, user.id, request.model_dump(exclude={"output"}, mode="json"), list(request.output))
        return QueryTaskSubmitted(task_id=int(task.task_id))
    except (DolphinSchedulerError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/tasks/{task_id}/outputs", response_model=list[QueryResultFile])
def list_query_results(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[QueryResultFile]:
    try:
        return query_result_files(session, user.id, task_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.get("/tasks/{task_id}/outputs/{name}", response_class=FileResponse)
def download_query_result(task_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FileResponse:
    try:
        path = query_result_path(session, user.id, task_id, name)
        return FileResponse(path, filename=path.name, media_type="application/vnd.apache.parquet")
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.get("/projects", response_model=QueryProjectPage)
def projects(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> QueryProjectPage:
    return QueryProjectPage.model_validate(list_query_projects(session, user.id, page, page_size))


@router.post("/projects", response_model=QueryProjectItem, status_code=status.HTTP_201_CREATED)
def create_project(request: QueryProjectCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryProjectItem:
    try:
        return QueryProjectItem.model_validate(create_query_project(session, user.id, request.title))
    except RuntimeError as error:
        raise_task_http_error(error)


@router.get("/projects/{project_id}", response_model=QueryProjectItem)
def project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryProjectItem:
    try:
        return QueryProjectItem.model_validate(get_query_project(session, user.id, project_id))
    except FileNotFoundError as error:
        raise_task_http_error(error)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"id": delete_query_project(session, user.id, project_id)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.post("/projects/{project_id}/queries", response_model=QueryProjectSubmitted, status_code=status.HTTP_202_ACCEPTED)
def run_project_query(project_id: int, request: FactorQuery, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryProjectSubmitted:
    try:
        task, reused = submit_project_query(session, user.id, project_id, {"dataset_query": request.model_dump(mode="json")})
        return QueryProjectSubmitted(record_id=task.id, task_id=int(task.task_id), reused=reused)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog)
def catalog(user: Annotated[User, Depends(get_current_user)]) -> DslCatalog:
    return DslCatalog.model_validate(query_dsl_catalog())
