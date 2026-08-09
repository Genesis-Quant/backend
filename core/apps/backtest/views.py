"""Backtest workflow, strategy project, version, and result endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from runtime import BacktestParameters
from sqlalchemy.orm import Session

from core.apps.backtest.schemas import (
    BacktestOutput,
    BacktestProjectCreate,
    BacktestProjectItem,
    BacktestProjectListItem,
    BacktestProjectUpdate,
    BacktestVersionCreate,
    BacktestVersionListItem,
    BacktestVersionResponse,
    BacktestVersionUpdate,
    BatchAnalysisType,
    BatchResearchCreate,
    BatchResearchListResponse,
    BatchResearchResponse,
    FeeAnalysisCreate,
)
from core.apps.backtest.services import (
    backtest_result_files,
    backtest_result_response,
    calculate_batch_research_results,
    create_batch_research,
    create_backtest_project,
    create_backtest_version,
    create_fee_analysis,
    delete_backtest_project,
    delete_backtest_version,
    get_backtest_project,
    get_backtest_version,
    get_batch_research,
    list_batch_research,
    list_backtest_projects,
    list_backtest_versions,
    submit_backtest_batch,
    submit_project_backtest,
    update_backtest_project,
    update_backtest_version,
)
from core.apps.schemas import BatchRunAccepted, BatchRunRequest, ProjectPage, WorkflowSubmitted
from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.services import current_workflow_instance
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog, dsl_catalog
from core.utils.http import raise_api_http_error
from core.utils.results import ResultFile

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


@router.get("/workflows/{workflow_instance_id}/outputs", response_model=list[ResultFile[BacktestOutput]])
def list_backtest_results(workflow_instance_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[ResultFile[BacktestOutput]]:
    try:
        return [ResultFile[BacktestOutput].model_validate(item) for item in backtest_result_files(session, user.id, workflow_instance_id)]
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/workflows/{workflow_instance_id}/outputs/{name}")
def download_backtest_result(workflow_instance_id: int, name: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> Response:
    try:
        return backtest_result_response(session, user.id, workflow_instance_id, name)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/projects", response_model=ProjectPage[BacktestProjectListItem])
def projects(user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)], page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> ProjectPage[BacktestProjectListItem]:
    return ProjectPage[BacktestProjectListItem].model_validate(list_backtest_projects(session, user.id, page, page_size))


@router.post("/projects", response_model=BacktestProjectItem, status_code=status.HTTP_201_CREATED)
def create_project(request: BacktestProjectCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    return BacktestProjectItem.model_validate(create_backtest_project(session, user.id, request.title))


@router.get("/projects/{project_id}", response_model=BacktestProjectItem)
def project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    try:
        return BacktestProjectItem.model_validate(get_backtest_project(session, user.id, project_id))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.patch("/projects/{project_id}", response_model=BacktestProjectItem)
def update_project(project_id: int, request: BacktestProjectUpdate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestProjectItem:
    try:
        return BacktestProjectItem.model_validate(update_backtest_project(session, user.id, project_id, request.title))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"id": delete_backtest_project(session, user.id, project_id)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/runs", response_model=WorkflowSubmitted, status_code=status.HTTP_202_ACCEPTED)
def run_project(project_id: int, request: BacktestParameters, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> WorkflowSubmitted:
    try:
        run = submit_project_backtest(session, user.id, project_id, request.model_dump(mode="json"))
        workflow = current_workflow_instance(session, run.id)
        if workflow is None:
            raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
        return WorkflowSubmitted(workspace_id=run.id, workflow_instance_id=workflow.workflow_instance_id)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions", response_model=list[BacktestVersionListItem])
def versions(project_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[BacktestVersionListItem]:
    try:
        return [BacktestVersionListItem.model_validate(item) for item in list_backtest_versions(session, user.id, project_id)]
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/versions", response_model=BacktestVersionResponse, status_code=status.HTTP_201_CREATED)
def save_version(project_id: int, request: BacktestVersionCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestVersionResponse:
    try:
        result = create_backtest_version(session, user.id, project_id, request.workflow_instance_id, request.remark)
        return BacktestVersionResponse.model_validate(result)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/projects/{project_id}/versions/{version_number}", response_model=BacktestVersionResponse)
def version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestVersionResponse:
    try:
        return BacktestVersionResponse.model_validate(get_backtest_version(session, user.id, project_id, version_number))
    except (FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/batch-runs", response_model=list[BatchRunAccepted], status_code=status.HTTP_202_ACCEPTED)
def execute_batch(project_id: int, request: BatchRunRequest[BacktestParameters], user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> list[BatchRunAccepted]:
    try:
        return [BatchRunAccepted.model_validate(item) for item in submit_backtest_batch(session, user.id, project_id, request.items)]
    except (FileNotFoundError, ValueError) as error:
        raise_api_http_error(error)


@router.patch("/projects/{project_id}/versions/{version_number}", response_model=BacktestVersionResponse)
def update_version(project_id: int, version_number: int, request: BacktestVersionUpdate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BacktestVersionResponse:
    try:
        return BacktestVersionResponse.model_validate(update_backtest_version(session, user.id, project_id, version_number, request.remark))
    except FileNotFoundError as error:
        raise_api_http_error(error)


@router.delete("/projects/{project_id}/versions/{version_number}")
def delete_version(project_id: int, version_number: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> dict[str, int]:
    try:
        return {"version": delete_backtest_version(session, user.id, project_id, version_number)}
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.get("/batch-research", response_model=BatchResearchListResponse)
def batch_researches(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    project_id: int | None = Query(None, gt=0),
    version: int | None = Query(None, gt=0),
    analysis_type: BatchAnalysisType | None = Query(None),
) -> BatchResearchListResponse:
    return BatchResearchListResponse.model_validate(
        list_batch_research(session, user, page, page_size, project_id=project_id, version=version, analysis_type=analysis_type)
    )


@router.post("/batch-research", response_model=BatchResearchResponse, status_code=status.HTTP_202_ACCEPTED)
def create_backtest_research(request: BatchResearchCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BatchResearchResponse:
    try:
        return BatchResearchResponse.model_validate(create_batch_research(session, user, request))
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/batch-research/{research_id}", response_model=BatchResearchResponse)
def backtest_research(research_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BatchResearchResponse:
    try:
        return BatchResearchResponse.model_validate(get_batch_research(session, user, research_id))
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.post("/batch-research/{research_id}/results", response_model=BatchResearchResponse)
def calculate_backtest_research_results(research_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BatchResearchResponse:
    try:
        return BatchResearchResponse.model_validate(calculate_batch_research_results(session, user, research_id))
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.post("/projects/{project_id}/versions/{version_number}/fee-analysis", response_model=BatchResearchResponse, status_code=status.HTTP_202_ACCEPTED)
def create_project_fee_analysis(project_id: int, version_number: int, request: FeeAnalysisCreate, user: Annotated[User, Depends(get_current_user)], session: Annotated[Session, Depends(get_database_session)]) -> BatchResearchResponse:
    try:
        return BatchResearchResponse.model_validate(create_fee_analysis(session, user, project_id, version_number, request))
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
        raise_api_http_error(error)


@router.get("/dsl/catalog", response_model=DslCatalog, dependencies=[Depends(get_current_user)])
def catalog() -> DslCatalog:
    return dsl_catalog()
