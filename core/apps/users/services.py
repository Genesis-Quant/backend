"""Password hashing and JWT Bearer authentication."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from core.apps.users.models import User
from config import AuthenticationSettings
from core.database.session import get_database_session

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(days=AuthenticationSettings.JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user.id), "username": user.username, "iat": now, "exp": expires}, AuthenticationSettings.JWT_SECRET, algorithm="HS256")


def validate_security_configuration() -> None:
    if not AuthenticationSettings.JWT_SECRET or len(AuthenticationSettings.JWT_SECRET) < 32:
        raise ValueError("ARENA_JWT_SECRET 必须至少包含 32 个字符")
    if AuthenticationSettings.JWT_EXPIRE_DAYS <= 0:
        raise ValueError("ARENA_JWT_EXPIRE_DAYS 必须是正整数")


def decode_user_id(token: str) -> int:
    try:
        payload = jwt.decode(token, AuthenticationSettings.JWT_SECRET, algorithms=["HS256"])
        return int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise authentication_error() from error


def authentication_error() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的凭据", headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_database_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise authentication_error()
    user = session.get(User, decode_user_id(credentials.credentials))
    if user is None:
        raise authentication_error()
    return user


def get_current_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user
