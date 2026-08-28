"""Shared MCP document, schema, and workflow helpers."""

from pathlib import Path
import math
from typing import Any

from pydantic import BaseModel

from config import MCPSettings
from core.apps.schemas import WorkflowSubmitted
from core.apps.users.schemas import McpConfiguration
from core.apps.users.services import MCP_DELETE_PERMISSION_FIELDS, mcp_configuration
from core.apps.workflows.services import current_workflow_instance
from core.database.session import database_session_factory
from core.scheduler.errors import DolphinSchedulerError

from .auth import current_user
from .schemas import DocumentName

DOCUMENT_DIRECTORY = Path(__file__).parents[2] / "docs"


def document(
    name: DocumentName,
    configuration: McpConfiguration | None = None,
) -> str:
    """Read one registered MCP Markdown document."""
    path = DOCUMENT_DIRECTORY / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"MCP 文档不存在：{name}")
    return (
        path.read_text(encoding="utf-8")
        .replace("{ARENA_PUBLIC_URL}", MCPSettings.PUBLIC_URL)
        .replace("{ARENA_WEB_URL}", MCPSettings.WEB_URL)
        .replace(
            "{ARENA_MCP_USER_CONFIGURATION}",
            render_mcp_configuration(configuration),
        )
    )


def authenticated_document(name: DocumentName) -> str:
    """Read a document and personalize the overview for the authenticated MCP user."""
    if name != "overview/overview":
        return document(name)
    with database_session_factory()() as session:
        user = current_user(session)
        return document(name, mcp_configuration(user))


def render_mcp_configuration(configuration: McpConfiguration | None) -> str:
    """Render the user prompt and all destructive permissions inside the overview."""
    if configuration is None:
        return ""
    statuses = {
        field: "允许" if getattr(configuration, field) else "禁止"
        for field in MCP_DELETE_PERMISSION_FIELDS
    }
    prompt = configuration.custom_prompt or "未设置自定义提示词。"
    return "\n".join((
        "## 当前用户 MCP 配置",
        "",
        "以下配置来自当前用户的个人主页。删除权限默认关闭；工具调用时服务端会再次校验，不能仅根据本文绕过。",
        "",
        (
            "- 项目删除："
            f"Query {statuses['allow_delete_query_projects']}；"
            f"Factor {statuses['allow_delete_factor_projects']}；"
            f"Backtest {statuses['allow_delete_backtest_projects']}。"
        ),
        (
            "- 版本删除："
            f"Factor {statuses['allow_delete_factor_versions']}；"
            f"Backtest {statuses['allow_delete_backtest_versions']}。"
        ),
        (
            "- 分析删除："
            f"手续费分析 {statuses['allow_delete_fee_analyses']}；"
            f"参数敏感性分析 {statuses['allow_delete_sensitivity_analyses']}；"
            f"参数调优 {statuses['allow_delete_optimizations']}。"
        ),
        "",
        "### 用户自定义提示词",
        "",
        prompt,
    ))


def runtime_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Expose Runtime schemas without falsely closing dynamic DSL fields."""
    schema = model.model_json_schema()
    derivative = schema.get("$defs", {}).get("Derivative")
    if derivative is None:
        return schema
    properties = derivative["properties"]
    properties["fields"] = {"type": "object", "description": "算符字段对象；精确字段由 describe_dsl_operator 返回。"}
    properties["params"] = {"type": "object", "description": "算符参数对象；精确参数由 describe_dsl_operator 返回。"}
    properties["on"] = {
        "anyOf": [{"type": "string"}, {"type": "boolean"}, {"$ref": "#/$defs/Derivative"}, {"type": "null"}],
        "default": None,
        "description": "TS/CS 算符可选的 BOOL 引用、常量或嵌套 DSL；DIRECT 算符禁止使用。",
    }
    return schema


def optional_parameter_count(value: Any) -> int | None:
    """Normalize defs() NaN parameter counts to JSON null."""
    if value is None or isinstance(value, float) and math.isnan(value):
        return None
    return int(value)


def submitted_workspace(session: Any, workspace_id: int) -> WorkflowSubmitted:
    """Return the scheduler instance created for a submitted workspace."""
    workflow = current_workflow_instance(session, workspace_id)
    if workflow is None:
        raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
    return WorkflowSubmitted(workspace_id=workspace_id, workflow_instance_id=workflow.workflow_instance_id)


__all__ = [
    "authenticated_document",
    "document",
    "optional_parameter_count",
    "render_mcp_configuration",
    "runtime_schema",
    "submitted_workspace",
]
