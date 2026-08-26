"""Assemble the Arena MCP server and Streamable HTTP application."""

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from config import MCPSettings

from .auth import ArenaTokenVerifier
from .views import register_views

SERVER_INSTRUCTIONS = (
    "Start with arena://docs/overview/overview, arena://docs/overview/projects, and "
    "arena://docs/overview/workflows. Before run_* read that application's request and API documents plus its "
    "Schema; backtests must also read arena://docs/backtest/dolphindb and "
    "arena://docs/backtest/interfaces before running and "
    "arena://docs/backtest/results before interpreting outputs. Read the applicable Backtest contract for dynamic "
    "data domains, quadratic-program outputs, callback objects, or result QA instead of guessing runtime behavior. "
    "arena://docs/overview/dolphindb documents the authenticated read-only DolphinScript diagnostic tool. Before constructing any "
    "FactorQuery, read arena://docs/overview/dsl: when an operator accepts nested DSL, keep single-use intermediate nodes nested "
    "and keep top-level factors/derivatives to the minimum required output, filter, or shared computation set. Discover DSL operators "
    "and DolphinDB signatures instead of guessing. Business results are in structuredContent.result. Follow "
    "create project -> run -> poll Workspace -> list outputs; only a successful current Factor/Backtest workflow "
    "can be saved. Use Workspace Attempt history to inspect earlier submitted parameters. Project/version rename, "
    "browser batch queues, fee studies, sensitivity studies, parameter optimization, task queries, and log downloads all have dedicated "
    "tools documented under the owning application or overview/workflows. No business deletion tool is exposed. "
    "The arbitrary DolphinScript tool uses a database account that cannot write or manage persistent data and limits "
    "each DolphinDB session to a ten-minute total lifetime, but it has no compute-resource sandbox."
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
        service_documentation_url=AnyHttpUrl(f"{MCPSettings.WEB_URL}/mcp"),
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
