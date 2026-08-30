"""Factor workflow, project, version, DSL catalog, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from core.apps.factor.schemas import (
    FactorOutput,
    FactorProjectCreate,
    FactorProjectItem,
    FactorProjectListItem,
    FactorProjectSortField,
    FactorProjectUpdate,
    FactorVersionCreate,
    FactorVersionListItem,
    FactorVersionResponse,
    FactorVersionUpdate,
)
from core.apps.factor.services import (
    create_factor_project,
    create_factor_version,
    delete_factor_project,
    delete_factor_version,
    factor_result_files,
    factor_result_response,
    get_factor_project,
    get_factor_version,
    list_factor_projects,
    list_factor_versions,
    submit_factor_batch,
    submit_project_analysis,
    update_factor_project,
    update_factor_version,
)
from core.apps.schemas import BatchRunAccepted, BatchRunRequest, ProjectPage, SortOrder, WorkflowSubmitted
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.services import current_workflow_instance
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog, PythonDslCompileError, dsl_catalog
from core.utils.dsl_source import (
    DslDocument,
    DslSource,
    FactorAnalysisApplicationRequest,
    compile_factor_dsl_source,
)
from core.utils.http import raise_api_http_error
from core.utils.results import ResultFile

router = APIRouter(prefix="/api/v1/factor", tags=["factor"])


@router.get("/workflows/{workflow_instance_id}/outputs", response_model=list[ResultFile[FactorOutput]])
def list_factor_results(workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[ResultFile[FactorOutput]]:
    try:
        return [ResultFile[FactorOutput].model_validate(item) for item in factor_result_files(session, user.id, workflow_instance_id)]
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs/{name}")
def download_factor_result(workflow_instance_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> Response:
    try:
        return factor_result_response(session, user.id, workflow_instance_id, name)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/projects", response_model=ProjectPage[FactorProjectListItem])
def projects(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=128),
    sort_by: FactorProjectSortField = Query("updated_at"),
    sort_order: SortOrder = Query("desc"),
) -> ProjectPage[FactorProjectListItem]:
    return ProjectPage[FactorProjectListItem].model_validate(
        list_factor_projects(
            session,
            user.id,
            page,
            page_size,
            search,
            sort_by,
            sort_order,
        )
    )


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


@router.post("/projects/{project_id}/analyses", response_model=WorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def analyze_project(project_id: int, request: FactorAnalysisApplicationRequest, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> WorkflowSubmitted:
    try:
        run = submit_project_analysis(
            session,
            user.id,
            project_id,
            request.stored_payload(),
        )
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return WorkflowSubmitted(workspace_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions", response_model=list[FactorVersionListItem])
def versions(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[FactorVersionListItem]:
    try:
        return [FactorVersionListItem.model_validate(item) for item in list_factor_versions(session, user.id, project_id)]
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/versions", response_model=FactorVersionResponse, status_code=status.HTTP_201_CREATED)
def save_version(project_id: int, request: FactorVersionCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorVersionResponse:
    try:
        result = create_factor_version(session, user.id, project_id, request.workflow_instance_id, request.remark)
        return FactorVersionResponse.model_validate(result)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions/{version_number}", response_model=FactorVersionResponse)
def version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorVersionResponse:
    try:
        return FactorVersionResponse.model_validate(get_factor_version(session, user.id, project_id, version_number))
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/batch-runs", response_model=list[BatchRunAccepted], status_code=status.HTTP_202_ACCEPTED)
def execute_batch(project_id: int, request: BatchRunRequest[FactorAnalysisApplicationRequest], user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[BatchRunAccepted]:
    try:
        return [BatchRunAccepted.model_validate(item) for item in submit_factor_batch(session, user.id, project_id, request.items)]
    except (FileNotFoundError, ValueError) as error:
        raise_api_http_error(error)


@router.patch("/projects/{project_id}/versions/{version_number}", response_model=FactorVersionResponse)
def update_version(project_id: int, version_number: int, request: FactorVersionUpdate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> FactorVersionResponse:
    try:
        return FactorVersionResponse.model_validate(update_factor_version(session, user.id, project_id, version_number, request.remark))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.delete("/projects/{project_id}/versions/{version_number}")
def delete_version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"version": delete_factor_version(session, user.id, project_id, version_number)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog, dependencies=[Depends(get_current_user)])
def catalog() -> DslCatalog:
    return dsl_catalog()


@router.post(
    "/dsl/compile",
    response_model=DslDocument,
    dependencies=[Depends(get_current_user)],
)
def compile_source(request: DslSource) -> DslDocument:
    try:
        return DslDocument.model_validate(compile_factor_dsl_source(request))
    except PythonDslCompileError as error:
        raise_api_http_error(error)
