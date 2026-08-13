"""MCP tool input aliases, output models, and annotations."""

from typing import Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel

from core.apps.backtest.schemas import BacktestProjectItem, BacktestProjectListItem, BacktestVersionListItem, BacktestVersionResponse
from core.apps.factor.schemas import FactorProjectItem, FactorProjectListItem, FactorVersionListItem, FactorVersionResponse
from core.apps.query.schemas import QueryProjectItem, QueryProjectListItem
from core.apps.schemas import ProjectPage
from core.utils.results import ResultFile

type ApplicationName = Literal["query", "factor", "backtest"]
type VersionedApplicationName = Literal["factor", "backtest"]
type DocumentName = Literal["overview", "tools", "query", "factor", "backtest", "dolphindb-backtest", "dsl"]
type ProjectListResult = ProjectPage[QueryProjectListItem] | ProjectPage[FactorProjectListItem] | ProjectPage[BacktestProjectListItem]
type ProjectResult = QueryProjectItem | FactorProjectItem | BacktestProjectItem
type VersionListResult = list[FactorVersionListItem | BacktestVersionListItem]
type VersionResult = FactorVersionResponse | BacktestVersionResponse

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
CONTROL = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)


class DslOperatorSummary(BaseModel):
    op: str
    type: Literal["DIRECT", "TS", "CS"]
    output_kind: Literal["BOOL", "NUMBER", "ANY"]
    description: str


class DslOperatorSearchResult(BaseModel):
    factors: list[str]
    operators: list[DslOperatorSummary]
    matched: int
    returned: int


class DolphinFunctionDefinition(BaseModel):
    name: str
    is_command: bool
    user_defined: bool
    min_parameter_count: int | None
    max_parameter_count: int | None
    syntax: str
    documentation_url: str


class DolphinFunctionDefinitions(BaseModel):
    requested: list[str]
    definitions: list[DolphinFunctionDefinition]
    missing: list[str]


class WorkflowOutputFile(ResultFile[str]):
    download_path: str


class WorkflowOutputs(BaseModel):
    application: ApplicationName
    workflow_instance_id: int
    outputs: list[WorkflowOutputFile]


class McpResult[T](BaseModel):
    """Stable envelope used by every Arena MCP tool."""

    result: T


__all__ = [
    "ApplicationName", "VersionedApplicationName", "DocumentName", "ProjectListResult", "ProjectResult",
    "VersionListResult", "VersionResult", "READ_ONLY", "WRITE", "CONTROL", "DslOperatorSummary",
    "DslOperatorSearchResult", "DolphinFunctionDefinition", "DolphinFunctionDefinitions",
    "WorkflowOutputFile", "WorkflowOutputs", "McpResult",
]
