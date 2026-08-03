"""Factor workflow, project, version, DSL catalog, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from runtime.apps.factor.schema import FactorAnalysisParameters
from sqlalchemy.orm import Session

from core.apps.factor.schemas import (
    FactorProjectCreate,
    FactorProjectItem,
    FactorProjectPage,
    FactorProjectUpdate,
    FactorResultFile,
    FactorVersionCreate,
    FactorVersionResponse,
    FactorWorkflowCreate,
    FactorWorkflowSubmitted,
)
from core.apps.factor.services import (
    create_factor_project,
    create_factor_version,
    delete_factor_project,
    dsl_catalog,
    factor_result_files,
    factor_result_path,
    get_factor_project,
    get_factor_version,
    list_factor_projects,
    list_factor_versions,
    submit_factor_workflow,
    submit_project_analysis,
    update_factor_project,
)
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.services import current_workflow_instance
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog
from core.utils.http import raise_api_http_error

router = APIRouter(prefix="/api/v1/factor", tags=["factor"])


@router.post("/workflows", response_model=FactorWorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def create_factor_workflow(request: FactorWorkflowCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorWorkflowSubmitted:
    try:
        run = submit_factor_workflow(session, user.id, request.model_dump(exclude={"output"}, mode="json"), list(request.output))
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return FactorWorkflowSubmitted(record_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs", response_model=list[FactorResultFile])
def list_factor_results(workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[FactorResultFile]:
    try:
        return factor_result_files(session, user.id, workflow_instance_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs/{name}", response_class=FileResponse)
def download_factor_result(workflow_instance_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FileResponse:
    try:
        path = factor_result_path(session, user.id, workflow_instance_id, name)
        return FileResponse(path, filename=path.name, media_type="application/vnd.apache.parquet")
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/projects", response_model=FactorProjectPage)
def projects(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> FactorProjectPage:
    return FactorProjectPage.model_validate(list_factor_projects(session, user.id, page, page_size))


@router.post("/projects", response_model=FactorProjectItem, status_code=status.HTTP_201_CREATED)
def create_project(request: FactorProjectCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorProjectItem:
    return FactorProjectItem.model_validate(create_factor_project(session, user.id, request.title))


@router.get("/projects/{project_id}", response_model=FactorProjectItem)
def project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorProjectItem:
    try:
        return FactorProjectItem.model_validate(get_factor_project(session, user.id, project_id))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.patch("/projects/{project_id}", response_model=FactorProjectItem)
def update_project(project_id: int, request: FactorProjectUpdate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorProjectItem:
    try:
        return FactorProjectItem.model_validate(update_factor_project(session, user.id, project_id, request.title))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"id": delete_factor_project(session, user.id, project_id)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/analyses", response_model=FactorWorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def analyze_project(project_id: int, request: FactorAnalysisParameters, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorWorkflowSubmitted:
    try:
        run = submit_project_analysis(session, user.id, project_id, request.model_dump(mode="json"))
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return FactorWorkflowSubmitted(record_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions", response_model=list[FactorVersionResponse])
def versions(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[FactorVersionResponse]:
    try:
        return [FactorVersionResponse.model_validate(item) for item in list_factor_versions(session, user.id, project_id)]
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/versions", response_model=FactorVersionResponse, status_code=status.HTTP_201_CREATED)
def save_version(project_id: int, request: FactorVersionCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorVersionResponse:
    try:
        result = create_factor_version(session, user.id, project_id, request.workflow_instance_id, request.remark, request.model_dump(mode="json")["metrics"])
        return FactorVersionResponse.model_validate(result)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions/{version_number}", response_model=FactorVersionResponse)
def version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorVersionResponse:
    try:
        return FactorVersionResponse.model_validate(get_factor_version(session, user.id, project_id, version_number))
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog)
def catalog(user: Annotated[User, Depends(get_current_user)]) -> DslCatalog:
    return DslCatalog.model_validate(dsl_catalog())
