"""Persist authenticated user feedback from supported clients."""

from sqlalchemy.orm import Session

from core.apps.users.models import User

from .models import Feedback
from .schemas import FeedbackCreate, FeedbackSource


def create_feedback(
    session: Session,
    user: User,
    request: FeedbackCreate,
    source: FeedbackSource,
) -> Feedback:
    feedback = Feedback(
        user_id=user.id,
        source=source,
        content=request.content,
    )
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback
