"""Authenticated read-only DolphinDB script execution for MCP diagnostics."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
import hashlib
import math
from time import monotonic
from typing import Annotated, Any

from mcp.server import MCPServer
import numpy as np
import pandas as pd
from pydantic import Field
from runtime.database import create_session as create_dolphindb_session
from runtime.utils import logger

from core.database.session import database_session_factory

from ..auth import current_user
from ..schemas import DolphinScriptResult, McpResult, SCRIPT

MAX_RESULT_VALUES = 100_000
MAX_RESULT_CHARACTERS = 1_000_000
MAX_RESULT_DEPTH = 32
MAX_SCRIPT_TIME_SECONDS = 10 * 60


def text_value(value: str, budget: list[int]) -> tuple[str, bool]:
    """Consume the shared text budget and return a bounded string."""
    length = min(len(value), budget[1])
    result = value[:length]
    budget[1] -= length
    return result, length < len(value)


def json_value(
    value: Any,
    limit: int,
    budget: list[int] | None = None,
    depth: int = 0,
) -> tuple[Any, bool]:
    """Convert DolphinDB client values to bounded JSON-compatible values."""
    if budget is None:
        budget = [MAX_RESULT_VALUES, MAX_RESULT_CHARACTERS]
    if depth >= MAX_RESULT_DEPTH or budget[0] <= 0:
        return None, True
    budget[0] -= 1
    if value is None or value is pd.NA or value is pd.NaT:
        return None, False
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return None, False
        return text_value(np.datetime_as_string(value, unit="auto"), budget)
    if isinstance(value, np.timedelta64):
        if np.isnat(value):
            return None, False
        return text_value(str(pd.Timedelta(value)), budget)
    if isinstance(value, np.generic):
        return json_value(value.item(), limit, budget, depth)
    if isinstance(value, (pd.Timestamp, datetime, date, time)):
        return text_value(value.isoformat(), budget)
    if isinstance(value, (pd.Timedelta, timedelta)):
        return text_value(str(value), budget)
    if isinstance(value, float):
        return (value if math.isfinite(value) else None), False
    if isinstance(value, str):
        return text_value(value, budget)
    if isinstance(value, (int, bool)):
        return value, False
    if isinstance(value, bytes):
        length = min(len(value), budget[1] // 2)
        result = value[:length].hex()
        budget[1] -= len(result)
        return result, length < len(value)
    if isinstance(value, pd.DataFrame):
        preview = value.iloc[:limit, :limit]
        converted, nested_truncated = json_value(preview.to_dict(orient="records"), limit, budget, depth + 1)
        return converted, len(value) > limit or len(value.columns) > limit or nested_truncated
    if isinstance(value, (pd.Series, pd.Index)):
        converted, nested_truncated = json_value(value[:limit].tolist(), limit, budget, depth + 1)
        return converted, len(value) > limit or nested_truncated
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        truncated = len(value) > limit
        for index, (key, item) in enumerate(value.items()):
            if index >= limit or budget[0] <= 0 or budget[1] <= 0:
                truncated = True
                break
            converted_key, key_truncated = text_value(str(key), budget)
            if key_truncated:
                truncated = True
                break
            converted, item_truncated = json_value(item, limit, budget, depth + 1)
            result[converted_key] = converted
            truncated = truncated or key_truncated or item_truncated
        return result, truncated
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return json_value(value[()], limit, budget, depth)
        truncated = len(value) > limit
        result = []
        for item in value[:limit]:
            converted, item_truncated = json_value(item, limit, budget, depth + 1)
            result.append(converted)
            truncated = truncated or item_truncated
            if budget[0] <= 0 or budget[1] <= 0:
                truncated = True
                break
        return result, truncated
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        truncated = len(value) > limit
        result = []
        for index, item in enumerate(value):
            if index >= limit or budget[0] <= 0 or budget[1] <= 0:
                truncated = True
                break
            converted, item_truncated = json_value(item, limit, budget, depth + 1)
            result.append(converted)
            truncated = truncated or item_truncated
        return result, truncated
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and missing:
        return None, False
    return text_value(str(value), budget)


def serialize_dolphindb_result(value: Any, max_rows: int) -> DolphinScriptResult:
    """Describe and serialize the value returned by the script's last expression."""
    python_type = f"{type(value).__module__}.{type(value).__qualname__}"
    if (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or isinstance(value, (np.datetime64, np.timedelta64)) and np.isnat(value)
    ):
        return DolphinScriptResult(kind="null", python_type=python_type)
    budget = [MAX_RESULT_VALUES, MAX_RESULT_CHARACTERS]
    if isinstance(value, pd.DataFrame):
        preview_columns = value.columns[:max_rows]
        preview = value.loc[:, preview_columns].head(max_rows)
        records, nested_truncated = json_value(preview.to_dict(orient="records"), max_rows, budget)
        return DolphinScriptResult(
            kind="table",
            python_type=python_type,
            row_count=len(value),
            column_count=len(value.columns),
            columns=[str(column) for column in preview_columns],
            truncated=len(value) > max_rows or len(value.columns) > max_rows or nested_truncated,
            value=records,
        )
    if isinstance(value, pd.Series):
        converted, nested_truncated = json_value(value.iloc[:max_rows].tolist(), max_rows, budget)
        return DolphinScriptResult(
            kind="vector",
            python_type=python_type,
            row_count=len(value),
            column_count=1,
            columns=[] if value.name is None else [str(value.name)],
            truncated=len(value) > max_rows or nested_truncated,
            value=converted,
        )
    if (
        isinstance(value, list)
        and len(value) == 3
        and isinstance(value[0], np.ndarray)
        and value[0].ndim == 2
    ):
        matrix = value[0]
        row_labels = None if value[1] is None else list(value[1])[:max_rows]
        column_labels = None if value[2] is None else list(value[2])[:max_rows]
        converted, nested_truncated = json_value(matrix[:max_rows, :max_rows], max_rows, budget)
        labels, labels_truncated = json_value(
            {"row_labels": row_labels, "column_labels": column_labels},
            max_rows,
            budget,
        )
        return DolphinScriptResult(
            kind="matrix",
            python_type=python_type,
            row_count=matrix.shape[0],
            column_count=matrix.shape[1],
            columns=[] if column_labels is None else [str(item) for item in column_labels],
            truncated=(
                matrix.shape[0] > max_rows
                or matrix.shape[1] > max_rows
                or nested_truncated
                or labels_truncated
            ),
            value={"data": converted, **labels},
        )
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            converted, truncated = json_value(value, max_rows, budget)
            return DolphinScriptResult(kind="scalar", python_type=python_type, truncated=truncated, value=converted)
        converted, nested_truncated = json_value(value[:max_rows], max_rows, budget)
        return DolphinScriptResult(
            kind="vector" if value.ndim == 1 else "matrix",
            python_type=python_type,
            row_count=len(value),
            column_count=1 if value.ndim == 1 else value.shape[1],
            truncated=len(value) > max_rows or nested_truncated,
            value=converted,
        )
    if isinstance(value, Mapping):
        converted, truncated = json_value(value, max_rows, budget)
        return DolphinScriptResult(kind="mapping", python_type=python_type, truncated=truncated, value=converted)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        converted, nested_truncated = json_value(value, max_rows, budget)
        return DolphinScriptResult(
            kind="vector",
            python_type=python_type,
            row_count=len(value),
            column_count=1,
            truncated=len(value) > max_rows or nested_truncated,
            value=converted,
        )
    converted, truncated = json_value(value, max_rows, budget)
    kind = "scalar" if isinstance(value, (str, int, float, bool, np.generic)) else "other"
    return DolphinScriptResult(kind=kind, python_type=python_type, truncated=truncated, value=converted)


def register_dolphindb_tools(server: MCPServer) -> None:
    """Register the authenticated read-only arbitrary script tool."""

    @server.tool(title="执行 DolphinDB 测试脚本", annotations=SCRIPT)
    def execute_dolphindb_script(
        script: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200_000,
                description=(
                    "要使用只读运行账号在服务端共享 DolphinDB 上原样执行的 DolphinScript。"
                    "任何已认证 Arena 用户均可调用；数据库写入、删改和管理操作会被 DolphinDB 拒绝。"
                    "DolphinDB session 从连接成功起最多使用 10 分钟；"
                    "工具返回脚本最后一个表达式的值。"
                ),
            ),
        ],
        max_rows: Annotated[
            int,
            Field(
                ge=1,
                le=2_000,
                description=(
                    "响应预览上限：表的行列、矩阵的行列及嵌套容器每层最多保留此前 N 项；"
                    "响应另有 100000 个值、1000000 个字符和 32 层嵌套的总上限；"
                    "不限制脚本计算量，也不改变脚本副作用。"
                ),
            ),
        ] = 200,
    ) -> McpResult[DolphinScriptResult]:
        """Execute arbitrary DolphinScript in a fresh authenticated session."""
        if not script.strip():
            raise ValueError("script 不能为空白")
        with database_session_factory()() as database_session:
            user = current_user(database_session)
            user_id = user.id

        digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:16]
        audit = f"user_id={user_id}, sha256={digest}, length={len(script)}"
        started_at = monotonic()
        logger.warning(f"MCP DolphinDB 任意脚本开始：{audit}")
        try:
            session = create_dolphindb_session(max_time=MAX_SCRIPT_TIME_SECONDS)
            try:
                result = session.run(script)
                response = McpResult(result=serialize_dolphindb_result(result, max_rows))
            finally:
                session.close()
        except Exception as error:
            logger.warning(
                f"MCP DolphinDB 任意脚本失败：{audit}, "
                f"duration={monotonic() - started_at:.3f}s, error={type(error).__name__}"
            )
            raise
        logger.warning(
            f"MCP DolphinDB 任意脚本完成：{audit}, "
            f"duration={monotonic() - started_at:.3f}s"
        )
        return response


__all__ = ["register_dolphindb_tools", "serialize_dolphindb_result"]
