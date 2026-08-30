"""Build validated Python DSL expressions without extending Runtime's public API."""

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


@lru_cache(maxsize=None)
def operator_candidates(
    expected_type: OperatorType,
    alias: str,
) -> tuple[tuple[str, type[Derivative]], ...]:
    normalized = alias[:-1] if alias.endswith("_") else alias
    candidates = [
        (operation, model)
        for operation, model in Derivative.operators.items()
        if operator_type(model) == expected_type
    ]
    exact = [
        candidate
        for candidate in candidates
        if candidate[0].replace(".", "_") == normalized
    ]
    if exact:
        return tuple(exact)
    return tuple(
        candidate
        for candidate in candidates
        if candidate[0].rsplit(".", 1)[-1] == normalized
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
    alias: str

    def __call__(
        self,
        name: str | None = None,
        *operands: Any,
        **arguments: Any,
    ) -> OP:
        candidates = operator_candidates(self.operator_type, self.alias)
        if not candidates:
            raise DslBuildError(f"不存在算符 {self.operator_type}.{self.alias}")

        matches: list[Derivative] = []
        failures: list[str] = []
        for operation, model in candidates:
            try:
                matches.append(
                    build_derivative(operation, model, operands, arguments)
                )
            except (TypeError, ValueError, ValidationError) as error:
                failures.append(f"{operation}: {error}")
        if not matches:
            raise DslBuildError(
                f"{self.operator_type}.{self.alias} 参数无效："
                + "; ".join(failures)
            )
        if len(matches) > 1:
            raise DslBuildError(
                f"{self.operator_type}.{self.alias} 调用存在歧义；"
                "请使用完整名称，例如 binary_add 或 multiary_add"
            )

        operation_dependencies: list[OP] = []
        seen: set[int] = set()
        for value in (*operands, *arguments.values()):
            for dependency in dependencies(value):
                if id(dependency) not in seen:
                    seen.add(id(dependency))
                    operation_dependencies.append(dependency)
        return OP(name, matches[0], tuple(operation_dependencies))


class OperatorNamespace(type):
    operator_type: OperatorType

    def __getattr__(cls, alias: str) -> Operator:
        if not alias or alias.startswith("_"):
            raise AttributeError(alias)
        return Operator(cls.operator_type, alias)


class DirectOperators(metaclass=OperatorNamespace):
    operator_type: OperatorType = "DIRECT"


class TimeSeriesOperators(metaclass=OperatorNamespace):
    operator_type: OperatorType = "TS"


class CrossSectionOperators(metaclass=OperatorNamespace):
    operator_type: OperatorType = "CS"


DIRECT = DirectOperators
TS = TimeSeriesOperators
CS = CrossSectionOperators


__all__ = [
    "CS",
    "DIRECT",
    "OP",
    "TS",
    "DslBuildError",
]
