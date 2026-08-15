"""MCP tool input aliases, output models, and annotations."""

from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from core.apps.backtest.schemas import BacktestProjectItem, BacktestProjectListItem, BacktestVersionListItem, BacktestVersionResponse
from core.apps.factor.schemas import FactorProjectItem, FactorProjectListItem, FactorVersionListItem, FactorVersionResponse
from core.apps.query.schemas import QueryProjectItem, QueryProjectListItem
from core.apps.schemas import ProjectPage
from core.utils.results import ResultFile

type ApplicationName = Literal["query", "factor", "backtest"]
type VersionedApplicationName = Literal["factor", "backtest"]
type DocumentName = Literal[
    "overview/overview",
    "overview/projects",
    "overview/dsl",
    "overview/dolphindb",
    "overview/workflows",
    "query/request",
    "query/api",
    "factor/request",
    "factor/api",
    "backtest/request",
    "backtest/api",
    "backtest/dolphindb",
    "backtest/results",
    "backtest/dynamic-pool",
    "backtest/optimization",
    "backtest/callback-data",
    "backtest/qa",
]
type ProjectListResult = ProjectPage[QueryProjectListItem] | ProjectPage[FactorProjectListItem] | ProjectPage[BacktestProjectListItem]
type ProjectResult = QueryProjectItem | FactorProjectItem | BacktestProjectItem
type VersionedProjectResult = FactorProjectItem | BacktestProjectItem
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


class DolphinScriptResult(BaseModel):
    kind: Literal["null", "scalar", "table", "vector", "matrix", "mapping", "other"]
    python_type: str
    row_count: int | None = None
    column_count: int | None = None
    columns: list[str] = Field(default_factory=list)
    truncated: bool = False
    value: Any = None


class WorkflowOutputFile(ResultFile[str]):
    download_path: str


class WorkflowOutputs(BaseModel):
    application: ApplicationName
    workflow_instance_id: int
    outputs: list[WorkflowOutputFile]


class TaskLogDownload(BaseModel):
    workflow_instance_id: int
    task_instance_id: int
    download_path: str


class McpBatchRunItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_id: str = Field(min_length=1, max_length=64)
    remark: str = Field(default="", max_length=512)
    parameters: dict[str, Any]


class McpResult[T](BaseModel):
    """Stable envelope used by every Arena MCP tool."""

    result: T


__all__ = [
    "ApplicationName", "VersionedApplicationName", "DocumentName", "ProjectListResult", "ProjectResult",
    "VersionedProjectResult", "VersionListResult", "VersionResult", "READ_ONLY", "WRITE", "CONTROL", "DslOperatorSummary",
    "DslOperatorSearchResult", "DolphinFunctionDefinition", "DolphinFunctionDefinitions", "DolphinScriptResult",
    "WorkflowOutputFile", "WorkflowOutputs", "TaskLogDownload", "McpBatchRunItem", "McpResult",
]
