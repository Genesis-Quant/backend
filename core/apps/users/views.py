"""User registration and login endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database.session import get_database_session
from core.apps.users.models import User
from core.apps.users.schemas import AuthenticationResponse, Credentials, UserResponse
from core.apps.users.services import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/v1")


@router.post("/auth/register", response_model=AuthenticationResponse, status_code=status.HTTP_201_CREATED, tags=["auth"])
def register(credentials: Credentials, session: Annotated[Session, Depends(get_database_session)]) -> AuthenticationResponse:
    if session.scalar(select(User.id).where(User.username == credentials.username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    has_admin = session.scalar(select(User.id).where(User.is_admin.is_(True))) is not None
    user = User(
        username=credentials.username,
        password_hash=hash_password(credentials.password),
        is_admin=not has_admin,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from error
    session.refresh(user)
    return authentication_response(user)


@router.post("/auth/login", response_model=AuthenticationResponse, tags=["auth"])
def login(credentials: Credentials, session: Annotated[Session, Depends(get_database_session)]) -> AuthenticationResponse:
    user = session.scalar(select(User).where(User.username == credentials.username))
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return authentication_response(user)


def authentication_response(user: User) -> AuthenticationResponse:
    return AuthenticationResponse(access_token=create_access_token(user), user=UserResponse.model_validate(user))


@router.get("/users/me", response_model=UserResponse, tags=["users"])
def current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user
