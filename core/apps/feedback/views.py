"""Authenticated HTTP endpoint for submitting product feedback."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core.apps.users.models import User
from core.apps.users.services import get_current_user
from core.database.session import get_database_session

from .schemas import FeedbackCreate, FeedbackResponse
from .services import create_feedback

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def submit_feedback(
    request: FeedbackCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> FeedbackResponse:
    return FeedbackResponse.model_validate(
        create_feedback(session, user, request, source="web")
    )
