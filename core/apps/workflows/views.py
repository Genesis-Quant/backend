"""Authenticated workflow instance endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.schemas import (
    WorkflowAction,
    WorkflowActionResponse,
    WorkflowAttemptInformation,
    WorkflowAttemptListResponse,
    WorkflowDeletedResponse,
    WorkflowInformation,
    WorkflowWorkspaceListResponse,
    WorkflowWorkspaceStatus,
    WorkflowStatusInformation,
    WorkflowTasks,
)
from core.apps.workflows.services import WorkflowGatewayService
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.http import raise_api_http_error

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.get("", response_model=WorkflowWorkspaceListResponse)
def list_workflows(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    application: Literal["query", "factor", "backtest", "optimization", "sensitivity", "incremental"] | None = None,
    state: Literal["active", "success", "failure"] | None = None,
) -> WorkflowWorkspaceListResponse:
    return WorkflowWorkspaceListResponse.model_validate(WorkflowGatewayService().list(session, user, page, page_size, application, state))


@router.get("/workspaces/{workspace_id}/attempts", response_model=WorkflowAttemptListResponse)
def list_workflow_attempts(
    workspace_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    include_tasks: bool = Query(False),
) -> WorkflowAttemptListResponse:
    try:
        return WorkflowAttemptListResponse.model_validate(
            WorkflowGatewayService().attempts(
                session,
                user,
                workspace_id,
                page,
                page_size,
                include_tasks,
            )
        )
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_api_http_error(error)


@router.get("/attempts/{attempt_id}", response_model=WorkflowAttemptInformation)
def get_workflow_attempt(
    attempt_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowAttemptInformation:
    try:
        return WorkflowAttemptInformation.model_validate(WorkflowGatewayService().attempt_detail(session, user, attempt_id))
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_api_http_error(error)


@router.get("/workspaces/{workspace_id}/status", response_model=WorkflowWorkspaceStatus)
def get_workflow_workspace_status(
    workspace_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowWorkspaceStatus:
    try:
        return WorkflowWorkspaceStatus.model_validate(WorkflowGatewayService().workspace_status(session, user, workspace_id))
    except (FileNotFoundError, OSError) as error:
        raise_api_http_error(error)


@router.get("/{workflow_instance_id}", response_model=WorkflowInformation)
def get_workflow(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowInformation:
    try:
        return WorkflowInformation.model_validate(WorkflowGatewayService().detail(session, user, workflow_instance_id))
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_api_http_error(error)


@router.get("/{workflow_instance_id}/status", response_model=WorkflowStatusInformation)
def get_workflow_status(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowStatusInformation:
    try:
        return WorkflowStatusInformation.model_validate(WorkflowGatewayService().status(session, user, workflow_instance_id))
    except (DolphinSchedulerError, FileNotFoundError, OSError) as error:
        raise_api_http_error(error)


@router.get("/{workflow_instance_id}/tasks", response_model=WorkflowTasks)
def get_workflow_tasks(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowTasks:
    try:
        return WorkflowTasks.model_validate(WorkflowGatewayService().tasks(session, user, workflow_instance_id))
    except (DolphinSchedulerError, FileNotFoundError) as error:
        raise_api_http_error(error)


@router.post(
    "/{workflow_instance_id}/actions/{action}",
    response_model=WorkflowActionResponse,
)
def control_workflow(
    workflow_instance_id: int,
    action: WorkflowAction,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowActionResponse:
    try:
        return WorkflowActionResponse.model_validate(WorkflowGatewayService().control(session, user, workflow_instance_id, action))
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)


@router.delete("/{workflow_instance_id}", response_model=WorkflowDeletedResponse)
def delete_workflow(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowDeletedResponse:
    try:
        return WorkflowDeletedResponse.model_validate(WorkflowGatewayService().delete(session, user, workflow_instance_id))
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)
