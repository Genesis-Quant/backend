"""Build validated Python DSL expressions without extending Runtime's public API."""

import keyword
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, get_args

from pydantic import ValidationError
from runtime.apps.query.schema import Derivative


OperatorType = Literal["DIRECT", "TS", "CS"]


class DslBuildError(ValueError):
    """Raised when a named Python DSL operation cannot be constructed."""


@dataclass(frozen=True, slots=True)
class OP:
    """Named reference to a validated Runtime DSL derivative."""

    name: str | None
    derivative: Derivative
    dependencies: tuple["OP", ...] = ()
    raw_field_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.name is None:
            return
        if not isinstance(self.name, str) or not self.name.strip():
            raise DslBuildError("算符名称必须是非空字符串或 None")
        object.__setattr__(self, "name", self.name.strip())


def operator_type(model: type[Derivative]) -> OperatorType:
    values = get_args(model.model_fields["type"].annotation)
    if len(values) != 1 or values[0] not in {"DIRECT", "TS", "CS"}:
        raise RuntimeError(f"{model.__name__}.type 不是有效的 DSL 类型")
    return values[0]


def normalize_member(name: str) -> str:
    """Map Python-safe keyword members such as ``and_`` back to ``and``."""
    if name.endswith("_") and keyword.iskeyword(name[:-1]):
        return name[:-1]
    return name


@lru_cache(maxsize=None)
def operator_model(
    expected_type: OperatorType,
    operation: str,
) -> type[Derivative] | None:
    model = Derivative.operators.get(operation)
    if model is None or operator_type(model) != expected_type:
        return None
    return model


@lru_cache(maxsize=None)
def operator_categories(expected_type: OperatorType) -> frozenset[str]:
    return frozenset(
        operation.split(".", 1)[0]
        for operation, model in Derivative.operators.items()
        if operator_type(model) == expected_type and "." in operation
    )


def dependencies(value: Any) -> list[OP]:
    if isinstance(value, OP):
        return [value] if value.name is not None else list(value.dependencies)
    if isinstance(value, (list, tuple)):
        return [dependency for item in value for dependency in dependencies(item)]
    if isinstance(value, dict):
        return [
            dependency
            for item in value.values()
            for dependency in dependencies(item)
        ]
    return []


def _raw_field_references(value: Any) -> list[str]:
    """Collect string field operands without confusing named OP references."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, OP):
        return list(value.raw_field_references) if value.name is None else []
    if isinstance(value, (list, tuple)):
        return [
            reference
            for item in value
            for reference in _raw_field_references(item)
        ]
    if isinstance(value, dict):
        return [
            reference
            for item in value.values()
            for reference in _raw_field_references(item)
        ]
    return []


def operand(value: Any) -> Any:
    if isinstance(value, OP):
        return value.name if value.name is not None else value.derivative
    if isinstance(value, (list, tuple)):
        return [operand(item) for item in value]
    if isinstance(value, dict):
        return {key: operand(item) for key, item in value.items()}
    return value


def build_derivative(
    operation: str,
    model: type[Derivative],
    operands: tuple[Any, ...],
    arguments: dict[str, Any],
) -> Derivative:
    fields_model = model.model_fields["fields"].annotation
    params_model = model.model_fields["params"].annotation
    field_names = tuple(fields_model.model_fields)
    param_names = set(params_model.model_fields)
    if len(operands) > len(field_names):
        raise ValueError(f"最多接收 {len(field_names)} 个位置操作数")

    fields = {
        field_name: operand(value)
        for field_name, value in zip(field_names, operands, strict=False)
    }
    params: dict[str, Any] = {}
    payload: dict[str, Any] = {
        "type": operator_type(model),
        "op": operation,
    }
    for name, value in arguments.items():
        if name in fields_model.model_fields:
            if name in fields:
                raise ValueError(f"字段 {name!r} 被重复传入")
            fields[name] = operand(value)
        elif name in param_names:
            params[name] = operand(value)
        elif name == "on" and "on" in model.model_fields:
            payload["on"] = operand(value)
        else:
            raise ValueError(f"不存在参数 {name!r}")
    payload["fields"] = fields
    payload["params"] = params
    return Derivative.model_validate(payload)


@dataclass(frozen=True, slots=True)
class Operator:
    operator_type: OperatorType
    operation: str
    register_named: Callable[[OP], None] | None = None

    def __call__(
        self,
        name: str | None = None,
        *operands: Any,
        **arguments: Any,
    ) -> OP:
        model = operator_model(self.operator_type, self.operation)
        if model is None:
            raise DslBuildError(
                f"不存在算符 {self.operator_type}.{self.operation}"
            )
        try:
            derivative = build_derivative(
                self.operation,
                model,
                operands,
                arguments,
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise DslBuildError(
                f"{self.operator_type}.{self.operation} 参数无效：{error}"
            ) from error

        operation_dependencies: list[OP] = []
        seen: set[int] = set()
        for value in (*operands, *arguments.values()):
            for dependency in dependencies(value):
                if id(dependency) not in seen:
                    seen.add(id(dependency))
                    operation_dependencies.append(dependency)
        fields_model = model.model_fields["fields"].annotation
        field_values = list(operands)
        field_values.extend(
            value
            for argument_name, value in arguments.items()
            if argument_name in fields_model.model_fields
        )
        nested_on = arguments.get("on")
        if isinstance(nested_on, OP):
            field_values.append(nested_on)
        literal_references = tuple(dict.fromkeys(
            reference
            for value in field_values
            for reference in _raw_field_references(value)
        ))
        result = OP(
            name,
            derivative,
            tuple(operation_dependencies),
            literal_references,
        )
        if result.name is not None and self.register_named is not None:
            self.register_named(result)
        return result


@dataclass(frozen=True, slots=True)
class OperatorCategory:
    operator_type: OperatorType
    category: str
    register_named: Callable[[OP], None] | None = None

    def __getattr__(self, member: str) -> Operator:
        if not member or member.startswith("_"):
            raise AttributeError(member)
        operation = f"{self.category}.{normalize_member(member)}"
        if operator_model(self.operator_type, operation) is None:
            raise AttributeError(member)
        return Operator(self.operator_type, operation, self.register_named)


@dataclass(frozen=True, slots=True)
class Operators:
    operator_type: OperatorType
    register_named: Callable[[OP], None] | None = None

    def __getattr__(self, member: str) -> OperatorCategory:
        if not member or member.startswith("_"):
            raise AttributeError(member)
        category = normalize_member(member)
        if category not in operator_categories(self.operator_type):
            raise AttributeError(member)
        return OperatorCategory(
            self.operator_type,
            category,
            self.register_named,
        )


DIRECT = Operators("DIRECT")
TS = Operators("TS")
CS = Operators("CS")


__all__ = [
    "CS",
    "DIRECT",
    "OP",
    "TS",
    "DslBuildError",
]
