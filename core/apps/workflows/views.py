"""Authenticated workflow instance endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.apps.workflows.schemas import (
    WorkflowAction,
    WorkflowActionResponse,
    WorkflowDeletedResponse,
    WorkflowInformation,
    WorkflowListResponse,
)
from core.apps.workflows.services import WorkflowGatewayService
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError
from core.utils.http import raise_api_http_error

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.get("", response_model=WorkflowListResponse)
def list_workflows(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    application: Literal["query", "factor", "backtest", "incremental"] | None = None,
    state: Literal["active", "success", "failure"] | None = None,
) -> WorkflowListResponse:
    return WorkflowGatewayService().list(session, user, page, page_size, application, state)


@router.get("/{workflow_instance_id}", response_model=WorkflowInformation)
def get_workflow(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowInformation:
    try:
        return WorkflowGatewayService().status(session, user, workflow_instance_id)
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
        return WorkflowGatewayService().control(session, user, workflow_instance_id, action)
    except (DolphinSchedulerError, FileNotFoundError, RuntimeError) as error:
        raise_api_http_error(error)


@router.delete("/{workflow_instance_id}", response_model=WorkflowDeletedResponse)
def delete_workflow(
    workflow_instance_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> WorkflowDeletedResponse:
    try:
        return WorkflowGatewayService().delete(session, user, workflow_instance_id)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        raise_api_http_error(error)
