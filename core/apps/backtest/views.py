"""Backtest task, strategy project, version, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from runtime.apps.backtest.schema import BacktestParameters
from sqlalchemy.orm import Session

from core.apps.backtest.schemas import (
    BacktestProjectCreate,
    BacktestProjectItem,
    BacktestProjectPage,
    BacktestProjectUpdate,
    BacktestResultFile,
    BacktestRunSubmitted,
    BacktestTaskCreate,
    BacktestTaskSubmitted,
    BacktestVersionCreate,
    BacktestVersionResponse,
)
from core.apps.backtest.services import (
    backtest_result_files,
    backtest_result_path,
    create_backtest_project,
    create_backtest_version,
    delete_backtest_project,
    dsl_catalog,
    get_backtest_project,
    get_backtest_version,
    list_backtest_projects,
    list_backtest_versions,
    submit_backtest_task,
    submit_project_backtest,
    update_backtest_project,
)
from core.apps.tasks.http import raise_task_http_error
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


@router.post("/tasks", response_model=BacktestTaskSubmitted, status_code=status.HTTP_202_ACCEPTED)
def create_backtest_task(request: BacktestTaskCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestTaskSubmitted:
    try:
        task = submit_backtest_task(session, user.id, request.model_dump(exclude={"output"}, mode="json"), list(request.output))
        return BacktestTaskSubmitted(task_id=int(task.task_id))
    except (DolphinSchedulerError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/tasks/{task_id}/outputs", response_model=list[BacktestResultFile])
def list_backtest_results(task_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[BacktestResultFile]:
    try:
        return backtest_result_files(session, user.id, task_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.get("/tasks/{task_id}/outputs/{name}", response_class=FileResponse)
def download_backtest_result(task_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FileResponse:
    try:
        path = backtest_result_path(session, user.id, task_id, name)
        return FileResponse(path, filename=path.name, media_type="application/vnd.apache.parquet")
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.get("/projects", response_model=BacktestProjectPage)
def projects(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> BacktestProjectPage:
    return BacktestProjectPage.model_validate(list_backtest_projects(session, user.id, page, page_size))


@router.post("/projects", response_model=BacktestProjectItem, status_code=status.HTTP_201_CREATED)
def create_project(request: BacktestProjectCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    return BacktestProjectItem.model_validate(create_backtest_project(session, user.id, request.title))


@router.get("/projects/{project_id}", response_model=BacktestProjectItem)
def project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    try:
        return BacktestProjectItem.model_validate(get_backtest_project(session, user.id, project_id))
    except FileNotFoundError as error:
        raise_task_http_error(error)


@router.patch("/projects/{project_id}", response_model=BacktestProjectItem)
def update_project(project_id: int, request: BacktestProjectUpdate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    try:
        return BacktestProjectItem.model_validate(update_backtest_project(session, user.id, project_id, request.title))
    except FileNotFoundError as error:
        raise_task_http_error(error)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"id": delete_backtest_project(session, user.id, project_id)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_task_http_error(error)


@router.post("/projects/{project_id}/runs", response_model=BacktestRunSubmitted, status_code=status.HTTP_202_ACCEPTED)
def run_project(project_id: int, request: BacktestParameters, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestRunSubmitted:
    try:
        task, reused = submit_project_backtest(session, user.id, project_id, request.model_dump(mode="json"))
        return BacktestRunSubmitted(record_id=task.id, task_id=int(task.task_id), reused=reused)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/projects/{project_id}/versions", response_model=list[BacktestVersionResponse])
def versions(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[BacktestVersionResponse]:
    try:
        return [BacktestVersionResponse.model_validate(item) for item in list_backtest_versions(session, user.id, project_id)]
    except (FileNotFoundError, RuntimeError) as error:
        raise_task_http_error(error)


@router.post("/projects/{project_id}/versions", response_model=BacktestVersionResponse, status_code=status.HTTP_201_CREATED)
def save_version(project_id: int, request: BacktestVersionCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestVersionResponse:
    try:
        result = create_backtest_version(session, user.id, project_id, request.task_id, request.remark, request.model_dump(mode="json")["summary"])
        return BacktestVersionResponse.model_validate(result)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_task_http_error(error)


@router.get("/projects/{project_id}/versions/{version_number}", response_model=BacktestVersionResponse)
def version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestVersionResponse:
    try:
        return BacktestVersionResponse.model_validate(get_backtest_version(session, user.id, project_id, version_number))
    except (FileNotFoundError, RuntimeError) as error:
        raise_task_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog)
def catalog(user: Annotated[User, Depends(get_current_user)]) -> DslCatalog:
    return DslCatalog.model_validate(dsl_catalog())
