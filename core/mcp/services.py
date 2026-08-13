"""Shared MCP document, schema, and workflow helpers."""

from pathlib import Path
import math
from typing import Any

from pydantic import BaseModel

from config import MCPSettings
from core.apps.schemas import WorkflowSubmitted
from core.apps.workflows.services import current_workflow_instance
from core.scheduler.errors import DolphinSchedulerError

from .schemas import DocumentName

DOCUMENT_DIRECTORY = Path(__file__).parents[2] / "docs"


def document(name: DocumentName) -> str:
    """Read one registered MCP Markdown document."""
    path = DOCUMENT_DIRECTORY / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"MCP 文档不存在：{name}")
    return (
        path.read_text(encoding="utf-8")
        .replace("{ARENA_PUBLIC_URL}", MCPSettings.PUBLIC_URL)
        .replace("{ARENA_WEB_URL}", MCPSettings.WEB_URL)
    )


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


__all__ = ["document", "runtime_schema", "optional_parameter_count", "submitted_workspace"]
