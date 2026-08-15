"""Register Arena MCP resources and tools."""

from mcp.server import MCPServer

from .accounts import register_account_tools
from .batch import register_batch_tools
from .discovery import register_discovery_tools
from .dolphindb import register_dolphindb_tools
from .projects import register_project_tools
from .resources import register_resources
from .workflows import register_workflow_tools


def register_views(server: MCPServer) -> None:
    register_resources(server)
    register_account_tools(server)
    register_discovery_tools(server)
    register_dolphindb_tools(server)
    register_project_tools(server)
    register_batch_tools(server)
    register_workflow_tools(server)


__all__ = ["register_views"]
