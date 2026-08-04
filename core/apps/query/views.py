"""Query projects, submissions, DSL catalog, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from runtime.apps.query.schema import FactorQuery
from sqlalchemy.orm import Session

from core.apps.query.schemas import (
    QueryProjectCreate,
    QueryProjectItem,
    QueryProjectPage,
    QueryResultFile,
    QueryWorkflowCreate,
    QueryWorkflowSubmitted,
)
from core.apps.query.services import (
    create_query_project,
    delete_query_project,
    get_query_project,
    list_query_projects,
    query_dsl_catalog,
    query_result_files,
    query_result_response,
    submit_project_query,
    submit_query_workflow,
)
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.services import current_workflow_instance
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog
from core.utils.http import raise_api_http_error

router = APIRouter(prefix="/api/v1/query", tags=["query"])


@router.post("/workflows", response_model=QueryWorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def create_query_workflow(request: QueryWorkflowCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryWorkflowSubmitted:
    try:
        run = submit_query_workflow(session, user.id, request.model_dump(exclude={"output"}, mode="json"), list(request.output))
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return QueryWorkflowSubmitted(record_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs", response_model=list[QueryResultFile])
def list_query_results(workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[QueryResultFile]:
    try:
        return query_result_files(session, user.id, workflow_instance_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs/{name}")
def download_query_result(workflow_instance_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> Response:
    try:
        return query_result_response(session, user.id, workflow_instance_id, name)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/projects", response_model=QueryProjectPage)
def projects(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> QueryProjectPage:
    return QueryProjectPage.model_validate(list_query_projects(session, user.id, page, page_size))


@router.post("/projects", response_model=QueryProjectItem, status_code=status.HTTP_201_CREATED)
def create_project(request: QueryProjectCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryProjectItem:
    try:
        return QueryProjectItem.model_validate(create_query_project(session, user.id, request.title))
    except RuntimeError as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}", response_model=QueryProjectItem)
def project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryProjectItem:
    try:
        return QueryProjectItem.model_validate(get_query_project(session, user.id, project_id))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"id": delete_query_project(session, user.id, project_id)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/queries", response_model=QueryWorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def run_project_query(project_id: int, request: FactorQuery, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> QueryWorkflowSubmitted:
    try:
        run = submit_project_query(session, user.id, project_id, {"dataset_query": request.model_dump(mode="json")})
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return QueryWorkflowSubmitted(record_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog)
def catalog(user: Annotated[User, Depends(get_current_user)]) -> DslCatalog:
    return DslCatalog.model_validate(query_dsl_catalog())
