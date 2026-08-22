"""Administrator-only HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.apps.admin.schemas import (
    AdminActionResponse,
    AdminOutputStorageResponse,
    AdminOverviewResponse,
    AdminUserListResponse,
    AdminUserUpdate,
    IncrementalUpdateRunCreate,
    IncrementalUpdateRunResponse,
)
from core.apps.admin.services import AdminService
from core.apps.users.models import User
from core.apps.users.schemas import UserResponse
from core.apps.users.services import get_current_admin
from core.database.session import get_database_session
from core.scheduler.errors import DolphinSchedulerError

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverviewResponse)
def overview(
    _: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> AdminOverviewResponse:
    return AdminOverviewResponse.model_validate(AdminService().overview(session))


@router.get("/output-storage", response_model=AdminOutputStorageResponse)
def output_storage(
    _: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> AdminOutputStorageResponse:
    return AdminOutputStorageResponse.model_validate(AdminService().output_storage(session))


@router.delete(
    "/output-storage/workspaces/{application}/{workspace_key}",
    response_model=AdminActionResponse,
)
def delete_orphan_workspace(
    application: str,
    workspace_key: str,
    _: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> dict[str, object]:
    try:
        result = AdminService().delete_orphan_workspace(
            session,
            application,
            workspace_key,
        )
        return {"message": "游离 workspace 已删除", "result": result}
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    _: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> dict[str, list[User]]:
    return {"items": AdminService().users(session)}


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    body: AdminUserUpdate,
    actor: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> User:
    try:
        return AdminService().update_user(session, actor, user_id, body.is_admin)
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/workflows/ensure", response_model=AdminActionResponse)
def ensure_workflows(
    _: Annotated[User, Depends(get_current_admin)],
) -> dict[str, object]:
    try:
        return {"message": "工作流定义已同步", "result": AdminService().ensure_workflows()}
    except DolphinSchedulerError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.post("/incremental-update/runs", response_model=IncrementalUpdateRunResponse)
def run_incremental_update(
    user: Annotated[User, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_database_session)],
    body: IncrementalUpdateRunCreate | None = None,
) -> dict[str, object]:
    try:
        return AdminService().run_incremental_update(
            session,
            user.id,
            None if body is None else body.workers,
            None if body is None else body.channel,
            False if body is None else body.overwrite,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except DolphinSchedulerError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
