"""Assemble the Arena MCP server and Streamable HTTP application."""

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from config import MCPSettings

from .auth import ArenaTokenVerifier
from .views import register_views

SERVER_INSTRUCTIONS = (
    "First read arena://docs/overview and arena://docs/tools. Before constructing a request, read the matching "
    "arena://docs resource and runtime JSON Schema. Before writing backtest callbacks, also read "
    "arena://docs/dolphindb-backtest. Inspect uncertain DolphinDB built-ins with describe_dolphindb_functions. "
    "Never invent DSL operator fields: call list_dsl_operators and describe_dsl_operator. Every tool's business "
    "result is at CallToolResult.structuredContent.result. Create or select a project before running a workflow. "
    "Poll get_workspace_status using the returned workspace_id until SUCCESS or a failure state, then call "
    "list_workflow_outputs. Factor and backtest versions can only be saved from a successful current workflow."
)

mcp_server = MCPServer(
    name="arena-quant",
    title="Arena Quant",
    description="Submit and inspect Arena query, factor-analysis, and backtest workflows.",
    instructions=SERVER_INSTRUCTIONS,
    version="0.1.0",
    token_verifier=ArenaTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(MCPSettings.PUBLIC_URL),
        resource_server_url=AnyHttpUrl(MCPSettings.ENDPOINT_URL),
        required_scopes=["arena"],
        service_documentation_url=AnyHttpUrl(f"{MCPSettings.PUBLIC_URL}/docs"),
    ),
)
register_views(mcp_server)

mcp_app = mcp_server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=MCPSettings.allowed_hosts(),
        allowed_origins=MCPSettings.allowed_origins(),
    ),
)

__all__ = ["mcp_app", "mcp_server"]
