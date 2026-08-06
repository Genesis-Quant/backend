"""Shared Factor Query DSL catalog."""

from typing import Any, Literal

from pydantic import BaseModel


class DslOperator(BaseModel):
    op: str
    type: Literal["DIRECT", "TS", "CS"]
    output_kind: Literal["BOOL", "NUMBER", "ANY"]
    description: str
    definition: dict[str, Any]


class DslCatalog(BaseModel):
    factors: list[str]
    operators: list[DslOperator]


DSL_CATALOG: DslCatalog | None = None


def initialize_dsl_catalog() -> DslCatalog:
    from runtime.apps.query.schema import Derivative
    from runtime.workers import available_factors

    operators = []
    for operation, model in sorted(Derivative.operators.items()):
        schema = model.model_json_schema()
        properties = schema.get("properties", {})
        type_schema = properties.get("type", {})
        operator_type = type_schema.get("const") or next(iter(type_schema.get("enum", [])), None)
        if operator_type not in {"DIRECT", "TS", "CS"}:
            continue
        operators.append({
            "op": operation,
            "type": operator_type,
            "output_kind": model.output_kind,
            "description": str(schema.get("description") or model.__doc__ or operation).strip(),
            "definition": schema,
        })
    global DSL_CATALOG
    DSL_CATALOG = DslCatalog.model_validate(
        {"factors": list(available_factors()), "operators": operators}
    )
    return DSL_CATALOG


def dsl_catalog() -> DslCatalog:
    if DSL_CATALOG is None:
        raise RuntimeError("DSL Catalog 尚未初始化")
    return DSL_CATALOG
