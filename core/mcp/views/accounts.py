"""MCP account tools available after Bearer authentication."""

from mcp.server import MCPServer

from core.apps.users.schemas import UserResponse
from core.database.session import database_session_factory

from ..auth import current_user
from ..schemas import McpResult, READ_ONLY


def register_account_tools(server: MCPServer) -> None:
    """Register the current authenticated user query."""

    @server.tool(title="获取当前用户", annotations=READ_ONLY)
    def get_current_user() -> McpResult[UserResponse]:
        """Return the user represented by the current Bearer token."""
        with database_session_factory()() as session:
            return McpResult(result=UserResponse.model_validate(current_user(session)))


__all__ = ["register_account_tools"]
