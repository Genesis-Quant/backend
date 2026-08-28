"""MCP tool for authenticated product feedback."""

from mcp.server import MCPServer

from core.apps.feedback.schemas import FeedbackContent, FeedbackCreate, FeedbackResponse
from core.apps.feedback.services import create_feedback
from core.database.session import database_session_factory

from ..auth import current_user
from ..schemas import McpResult, WRITE


def register_feedback_tools(server: MCPServer) -> None:
    """Register feedback submission for the current Bearer-token user."""

    @server.tool(title="提交用户反馈", annotations=WRITE)
    def submit_feedback(
        content: FeedbackContent,
    ) -> McpResult[FeedbackResponse]:
        """Persist feedback for the authenticated Arena user and mark its source as MCP."""
        request = FeedbackCreate(content=content)
        with database_session_factory()() as session:
            feedback = create_feedback(
                session,
                current_user(session),
                request,
                source="mcp",
            )
            return McpResult(result=FeedbackResponse.model_validate(feedback))


__all__ = ["register_feedback_tools"]
