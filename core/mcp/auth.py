"""Arena JWT authentication for MCP requests."""

from typing import Any

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier

from config import MCPSettings
from core.apps.users.models import User
from core.apps.users.services import decode_user_id


class ArenaTokenVerifier(TokenVerifier):
    """Validate JWTs issued by the Arena login endpoint."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            user_id = decode_user_id(token)
        except HTTPException:
            return None
        return AccessToken(
            token=token,
            client_id=str(user_id),
            subject=str(user_id),
            scopes=["arena"],
            resource=MCPSettings.ENDPOINT_URL,
        )


def current_user(session: Any) -> User:
    """Resolve the authenticated Arena user in the current database session."""
    token = get_access_token()
    if token is None or token.subject is None:
        raise PermissionError("MCP 请求缺少有效的 Arena Bearer Token")
    try:
        user_id = int(token.subject)
    except ValueError as error:
        raise PermissionError("Arena Bearer Token 不包含有效用户") from error
    user = session.get(User, user_id)
    if user is None:
        raise PermissionError("Arena 用户不存在")
    return user


__all__ = ["ArenaTokenVerifier", "current_user"]
