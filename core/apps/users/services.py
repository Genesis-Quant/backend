"""Password hashing and JWT Bearer authentication."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from config import AuthenticationSettings
from core.apps.users.models import User
from core.apps.users.schemas import McpConfiguration
from core.database.session import get_database_session

bearer_scheme = HTTPBearer(auto_error=False)

MCP_DELETE_PERMISSION_FIELDS = {
    "allow_delete_query_projects": ("mcp_allow_delete_query_projects", "数据查询项目"),
    "allow_delete_factor_projects": ("mcp_allow_delete_factor_projects", "因子分析项目"),
    "allow_delete_backtest_projects": ("mcp_allow_delete_backtest_projects", "策略回测项目"),
    "allow_delete_factor_versions": ("mcp_allow_delete_factor_versions", "因子分析版本"),
    "allow_delete_backtest_versions": ("mcp_allow_delete_backtest_versions", "策略回测版本"),
    "allow_delete_fee_analyses": ("mcp_allow_delete_fee_analyses", "手续费分析"),
    "allow_delete_sensitivity_analyses": ("mcp_allow_delete_sensitivity_analyses", "参数敏感性分析"),
    "allow_delete_optimizations": ("mcp_allow_delete_optimizations", "参数调优报告"),
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def mcp_configuration(user: User) -> McpConfiguration:
    """Serialize one user's MCP configuration."""
    return McpConfiguration(
        custom_prompt=user.mcp_custom_prompt,
        **{
            field: bool(getattr(user, attribute))
            for field, (attribute, _) in MCP_DELETE_PERMISSION_FIELDS.items()
        },
    )


def update_mcp_configuration(
    session: Session,
    user: User,
    configuration: McpConfiguration,
) -> McpConfiguration:
    """Replace one user's complete MCP configuration."""
    user.mcp_custom_prompt = configuration.custom_prompt
    for field, (attribute, _) in MCP_DELETE_PERMISSION_FIELDS.items():
        setattr(user, attribute, getattr(configuration, field))
    session.commit()
    session.refresh(user)
    return mcp_configuration(user)


def require_mcp_delete_permission(user: User, field: str) -> None:
    """Reject a destructive MCP operation unless its explicit user switch is enabled."""
    permission = MCP_DELETE_PERMISSION_FIELDS.get(field)
    if permission is None:
        raise ValueError(f"未知 MCP 删除权限：{field}")
    attribute, label = permission
    if not getattr(user, attribute):
        raise PermissionError(f"MCP 删除{label}未启用；请先在个人主页的 MCP 配置中开启对应权限")


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(days=AuthenticationSettings.JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user.id), "username": user.username, "iat": now, "exp": expires}, jwt_secret(), algorithm="HS256")


def validate_security_configuration() -> None:
    jwt_secret()
    if AuthenticationSettings.JWT_EXPIRE_DAYS <= 0:
        raise ValueError("ARENA_JWT_EXPIRE_DAYS 必须是正整数")


def jwt_secret() -> str:
    secret = AuthenticationSettings.JWT_SECRET
    if not secret or len(secret) < 32:
        raise ValueError("ARENA_JWT_SECRET 必须至少包含 32 个字符")
    return secret


def decode_user_id(token: str) -> int:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
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
