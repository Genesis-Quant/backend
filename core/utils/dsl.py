"""Shared Factor Query DSL catalog and Python declaration compiler."""

import ast
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from runtime.apps.query.schema import Derivative, FactorQuery

from core.utils.dsl_builder import (
    CS,
    DIRECT,
    OP,
    TS,
    normalize_member,
    operator_model,
)


OperatorType = Literal["DIRECT", "TS", "CS"]
PYTHON_DSL_AST_MAX_NODES = 50_000
PYTHON_DSL_EXECUTION_MAX_ITEMS = 30_000


class PythonDslCompileError(ValueError):
    """Raised when Python DSL source or its declared result is invalid."""


class _DslSyntaxValidator:
    """Allow bounded Python composition around DSL calls."""

    namespaces = frozenset({"DIRECT", "TS", "CS"})
    safe_builtins = frozenset({"range", "zip"})

    def __init__(self) -> None:
        self.function_names: set[str] = set()
        self.function_parameters: set[str] = set()
        self.inside_function = False
        self.comprehension_depth = 0

    def validate(self, module: ast.Module) -> None:
        self.function_names = {
            statement.name
            for statement in module.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for statement in module.body:
            if isinstance(statement, ast.Assign):
                if (
                    len(statement.targets) != 1
                    or not isinstance(statement.targets[0], ast.Name)
                ):
                    self._fail(statement, "只允许给单个变量赋值")
                self._validate_target(statement.targets[0])
                self._validate_expression(statement.value)
            elif isinstance(statement, ast.AnnAssign):
                if not isinstance(statement.target, ast.Name) or statement.value is None:
                    self._fail(statement, "类型标注变量必须直接赋值")
                self._validate_target(statement.target)
                self._validate_expression(statement.value)
            elif isinstance(statement, ast.FunctionDef):
                self._validate_function(statement)
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            else:
                self._fail(
                    statement,
                    "只允许变量声明、安全辅助函数和 DSL 算符调用",
                )

    def _validate_target(self, target: ast.Name) -> None:
        if target.id in self.namespaces or target.id.startswith("__"):
            self._fail(target, f"不能覆盖保留名称 {target.id!r}")

    def _validate_expression(self, expression: ast.expr) -> None:
        if isinstance(expression, ast.Constant):
            if expression.value is None or isinstance(
                expression.value,
                (str, int, float, bool),
            ):
                return
        elif isinstance(expression, ast.Name):
            if not expression.id.startswith("__"):
                return
        elif isinstance(expression, (ast.List, ast.Tuple)):
            for item in expression.elts:
                if isinstance(item, ast.Starred):
                    self._validate_expression(item.value)
                else:
                    self._validate_expression(item)
            return
        elif isinstance(expression, ast.Dict):
            for key, value in zip(expression.keys, expression.values, strict=True):
                if key is None:
                    self._fail(expression, "不支持字典展开")
                self._validate_expression(key)
                self._validate_expression(value)
            return
        elif isinstance(expression, ast.UnaryOp) and isinstance(
            expression.op,
            (ast.UAdd, ast.USub, ast.Not),
        ):
            self._validate_expression(expression.operand)
            return
        elif isinstance(expression, ast.Call):
            self._validate_call(expression)
            return
        elif isinstance(expression, ast.Attribute):
            self._validate_operator_attribute(expression)
            return
        elif isinstance(expression, ast.Subscript):
            self._validate_expression(expression.value)
            self._validate_expression(expression.slice)
            return
        elif isinstance(expression, ast.ListComp):
            self._validate_comprehension(expression.elt, expression.generators)
            return
        elif isinstance(expression, ast.JoinedStr):
            for value in expression.values:
                if isinstance(value, ast.FormattedValue):
                    self._validate_expression(value.value)
                    if value.format_spec is not None:
                        self._validate_format_spec(value.format_spec)
                elif not isinstance(value, ast.Constant):
                    self._fail(value, "格式化字符串包含不支持的表达式")
            return
        self._fail(expression, f"不支持表达式 {type(expression).__name__}")

    def _validate_call(self, expression: ast.Call) -> None:
        function = expression.func
        if isinstance(function, ast.Attribute):
            self._validate_operator_attribute(function)
        elif isinstance(function, ast.Name):
            callable_names = self.safe_builtins
            if not self.inside_function:
                callable_names |= self.function_names
            if function.id not in callable_names:
                self._fail(expression, f"不允许调用函数 {function.id!r}")
            if function.id == "range":
                self._validate_range(expression)
        else:
            self._fail(expression, "不支持该函数调用方式")
        if any(isinstance(argument, ast.Starred) for argument in expression.args):
            self._fail(expression, "不支持位置参数展开")
        if any(keyword.arg is None for keyword in expression.keywords):
            self._fail(expression, "不支持关键字参数展开")
        for argument in expression.args:
            self._validate_expression(argument)
        for keyword in expression.keywords:
            self._validate_expression(keyword.value)

    def _validate_operator_attribute(self, expression: ast.Attribute) -> None:
        category = expression.value
        if (
            not isinstance(category, ast.Attribute)
            or not isinstance(category.value, ast.Name)
            or category.value.id not in self.namespaces
            or category.attr.startswith("_")
            or expression.attr.startswith("_")
        ):
            self._fail(
                expression,
                "只能引用分层 DSL 算符，例如 DIRECT.binary.div",
            )
        namespace = cast(OperatorType, category.value.id)
        operation = f"{category.attr}.{normalize_member(expression.attr)}"
        if operator_model(namespace, operation) is None:
            self._fail(
                expression,
                f"不存在 DSL 算符 {namespace}.{category.attr}."
                f"{expression.attr}",
            )

    def _validate_function(self, statement: ast.FunctionDef) -> None:
        self._validate_target(ast.Name(id=statement.name, ctx=ast.Store()))
        arguments = statement.args
        if (
            statement.decorator_list
            or statement.returns is not None
            or arguments.posonlyargs
            or arguments.vararg is not None
            or arguments.kwonlyargs
            or arguments.kwarg is not None
            or arguments.defaults
            or arguments.kw_defaults
            or any(argument.annotation is not None for argument in arguments.args)
        ):
            self._fail(statement, "辅助函数只支持无装饰器、无默认值的普通参数")
        if len(statement.body) != 1 or not isinstance(statement.body[0], ast.Return):
            self._fail(statement, "辅助函数必须只包含一个 return")
        returned = statement.body[0].value
        if returned is None:
            self._fail(statement, "辅助函数必须返回 DSL 表达式")

        previous_inside = self.inside_function
        previous_parameters = self.function_parameters
        self.inside_function = True
        self.function_parameters = {argument.arg for argument in arguments.args}
        try:
            for name in self.function_parameters:
                self._validate_target(ast.Name(id=name, ctx=ast.Param()))
            self._validate_expression(returned)
        finally:
            self.inside_function = previous_inside
            self.function_parameters = previous_parameters

    def _validate_comprehension(
        self,
        element: ast.expr,
        generators: list[ast.comprehension],
    ) -> None:
        if self.comprehension_depth > 0:
            self._fail(element, "不支持嵌套推导式")
        if len(generators) != 1:
            self._fail(element, "推导式只能包含一层循环")
        self.comprehension_depth += 1
        try:
            generator = generators[0]
            if generator.is_async:
                self._fail(generator, "不支持异步推导式")
            self._validate_comprehension_target(generator.target)
            self._validate_expression(generator.iter)
            for condition in generator.ifs:
                self._validate_expression(condition)
            self._validate_expression(element)
        finally:
            self.comprehension_depth -= 1

    def _validate_format_spec(self, expression: ast.expr) -> None:
        if not isinstance(expression, ast.JoinedStr) or any(
            not isinstance(value, ast.Constant)
            or not isinstance(value.value, str)
            for value in expression.values
        ):
            self._fail(expression, "格式化字符串不支持动态格式参数")
        specification = "".join(
            value.value
            for value in expression.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
        if len(specification) > 32 or any(
            int(number) > 1_000
            for number in re.findall(r"\d+", specification)
        ):
            self._fail(expression, "格式化字符串的宽度或精度过大")

    def _validate_comprehension_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self._validate_target(target)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._validate_comprehension_target(item)
            return
        self._fail(target, "推导式只能使用变量或变量元组")

    def _validate_range(self, expression: ast.Call) -> None:
        if expression.keywords or not 1 <= len(expression.args) <= 3:
            self._fail(expression, "range 只支持 1 至 3 个整数常量参数")
        values: list[int] = []
        for argument in expression.args:
            value: int | None = None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, int):
                value = argument.value
            elif (
                isinstance(argument, ast.UnaryOp)
                and isinstance(argument.op, (ast.UAdd, ast.USub))
                and isinstance(argument.operand, ast.Constant)
                and isinstance(argument.operand.value, int)
            ):
                value = (
                    argument.operand.value
                    if isinstance(argument.op, ast.UAdd)
                    else -argument.operand.value
                )
            if value is None:
                self._fail(argument, "range 参数必须是整数常量")
            values.append(value)
        try:
            size = len(range(*values))
        except ValueError as error:
            self._fail(expression, f"range 参数无效：{error}")
        if size > 10_000:
            self._fail(expression, "range 最多生成 10000 个值")

    @staticmethod
    def _fail(node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", None)
        prefix = f"第 {line} 行：" if line is not None else ""
        raise PythonDslCompileError(prefix + message)


class _DslExecutionBudget:
    """Bound the total number of values materialized by dynamic composition."""

    def __init__(self) -> None:
        self.items = 0

    def iterate(self, value: Any) -> Any:
        for item in value:
            self.items += 1
            if self.items > PYTHON_DSL_EXECUTION_MAX_ITEMS:
                raise PythonDslCompileError(
                    "Python DSL 推导式和列表展开累计生成的值不能超过 "
                    f"{PYTHON_DSL_EXECUTION_MAX_ITEMS} 个"
                )
            yield item


class _PreparePythonDsl(ast.NodeTransformer):
    """Remove annotations and meter every comprehension or starred expansion."""

    budget_function = "__dsl_bounded_iter"

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.Assign:
        if node.value is None:
            raise PythonDslCompileError("类型标注变量必须直接赋值")
        return ast.copy_location(
            ast.Assign(targets=[node.target], value=self.visit(node.value)),
            node,
        )

    def visit_ListComp(self, node: ast.ListComp) -> ast.ListComp:
        prepared = self.generic_visit(node)
        for generator in prepared.generators:
            generator.iter = self._bounded(generator.iter)
        return prepared

    def visit_Starred(self, node: ast.Starred) -> ast.Starred:
        return ast.copy_location(
            ast.Starred(
                value=self._bounded(self.visit(node.value)),
                ctx=node.ctx,
            ),
            node,
        )

    def _bounded(self, value: ast.expr) -> ast.Call:
        return ast.Call(
            func=ast.Name(id=self.budget_function, ctx=ast.Load()),
            args=[value],
            keywords=[],
        )


def _execute_python_dsl(source: str) -> dict[str, Any]:
    try:
        module = ast.parse(source, filename="<python-dsl>", mode="exec")
    except SyntaxError as error:
        location = f"第 {error.lineno} 行" if error.lineno is not None else "Python DSL"
        raise PythonDslCompileError(f"{location}：{error.msg}") from error
    if sum(1 for _ in ast.walk(module)) > PYTHON_DSL_AST_MAX_NODES:
        raise PythonDslCompileError(
            "Python DSL 语法节点不能超过 "
            f"{PYTHON_DSL_AST_MAX_NODES} 个"
        )

    _DslSyntaxValidator().validate(module)
    executable = _PreparePythonDsl().visit(module)
    ast.fix_missing_locations(executable)
    budget = _DslExecutionBudget()
    namespace: dict[str, Any] = {
        "__builtins__": {
            "range": range,
            "zip": zip,
        },
        "DIRECT": DIRECT,
        "TS": TS,
        "CS": CS,
        _PreparePythonDsl.budget_function: budget.iterate,
    }
    try:
        exec(compile(executable, "<python-dsl>", "exec"), namespace, namespace)
    except PythonDslCompileError:
        raise
    except Exception as error:
        raise PythonDslCompileError(f"Python DSL 执行失败：{error}") from error
    return namespace


def _operation_list(namespace: dict[str, Any], name: str) -> list[OP]:
    value = namespace[name]
    if not isinstance(value, list):
        raise PythonDslCompileError(f"{name} 必须是 list[OP]")
    for index, item in enumerate(value):
        if not isinstance(item, OP):
            raise PythonDslCompileError(
                f"{name}[{index}] 必须是 OP，实际为 {type(item).__name__}"
            )
        if item.name is None:
            raise PythonDslCompileError(f"{name}[{index}] 必须是有名称的 OP")
    return value


def _definitions(derivatives: list[OP], filters: list[OP]) -> dict[str, Derivative]:
    result: dict[str, Derivative] = {}
    objects: dict[str, OP] = {}
    visited: set[int] = set()

    def visit(operation: OP) -> None:
        if id(operation) in visited:
            return
        if operation.name is None:
            raise PythonDslCompileError("DERIVATIVES 和 FILTERS 只能包含有名称的 OP")
        if operation.name in objects and objects[operation.name] is not operation:
            raise PythonDslCompileError(f"算符名称重复：{operation.name!r}")
        objects[operation.name] = operation
        for dependency in operation.dependencies:
            visit(dependency)
        visited.add(id(operation))
        result[operation.name] = operation.derivative

    for operation in [*derivatives, *filters]:
        visit(operation)
    return result


def compile_python_dsl(
    source: str,
    *,
    external_derivatives: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute declarations and return current FactorQuery DSL JSON fields."""
    if not isinstance(source, str):
        raise PythonDslCompileError("Python DSL 源代码必须是字符串")
    if len(source) > 100_000:
        raise PythonDslCompileError("Python DSL 源代码不能超过 100000 个字符")

    namespace = _execute_python_dsl(source)
    required = ("FACTORS", "DERIVATIVES", "FILTERS")
    if missing := [name for name in required if name not in namespace]:
        raise PythonDslCompileError(
            "Python DSL 必须定义变量：" + ", ".join(missing)
        )

    factors = namespace["FACTORS"]
    if not isinstance(factors, list) or any(
        not isinstance(item, str) for item in factors
    ):
        raise PythonDslCompileError("FACTORS 必须是 list[str]")
    derivatives = _operation_list(namespace, "DERIVATIVES")
    filters = _operation_list(namespace, "FILTERS")

    definitions = _definitions(derivatives, filters)
    try:
        query = FactorQuery.model_validate({
            "start_date": "2000-01-01",
            "end_date": "2000-01-01",
            "lookback": "P0D",
            "codes": [],
            "factors": factors,
            "derivatives": {
                **(external_derivatives or {}),
                **definitions,
            },
            "filters": [operation.name for operation in filters],
        })
    except ValidationError as error:
        raise PythonDslCompileError(f"Python DSL 结果无效：{error}") from error

    return {
        "factors": query.factors,
        "derivatives": {
            name: query.derivatives[name].model_dump(mode="json")
            for name in definitions
        },
        "filters": query.filters,
    }


class DslOperator(BaseModel):
    op: str
    type: OperatorType
    output_kind: Literal["BOOL", "NUMBER", "ANY"]
    description: str
    definition: dict[str, Any]


class DslCatalog(BaseModel):
    factors: list[str]
    operators: list[DslOperator]


DSL_CATALOG: DslCatalog | None = None


def initialize_dsl_catalog() -> DslCatalog:
    from runtime.workers import available_factors

    operators = []
    for operation, model in sorted(Derivative.operators.items()):
        schema = model.model_json_schema()
        properties = schema.get("properties", {})
        type_schema = properties.get("type", {})
        operator_type = type_schema.get("const") or next(
            iter(type_schema.get("enum", [])),
            None,
        )
        if operator_type not in {"DIRECT", "TS", "CS"}:
            continue
        operators.append({
            "op": operation,
            "type": operator_type,
            "output_kind": model.output_kind,
            "description": str(
                schema.get("description") or model.__doc__ or operation
            ).strip(),
            "definition": schema,
        })
    global DSL_CATALOG
    DSL_CATALOG = DslCatalog.model_validate({
        "factors": list(available_factors()),
        "operators": operators,
    })
    return DSL_CATALOG


def dsl_catalog() -> DslCatalog:
    if DSL_CATALOG is None:
        raise RuntimeError("DSL Catalog 尚未初始化")
    return DSL_CATALOG
