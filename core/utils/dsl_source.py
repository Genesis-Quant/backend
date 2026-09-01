"""Source-aware DSL request models compiled before Runtime submission."""

from __future__ import annotations

import json
import keyword
import re
from datetime import timedelta
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.apps.backtest.schema import BacktestParameters
from runtime.apps.factor.schema import (
    FactorAnalysisParameters,
    FactorReturnSpec,
)
from runtime.apps.query.schema import FactorQuery

from core.utils.dsl import PythonDslCompileError, compile_python_dsl


FACTOR_MANAGED_COLUMNS = frozenset({"circ_mv", "total_mv"})
FACTOR_STOCK_POOL_FACTORS = frozenset({
    "weight_000016SH",
    "weight_000300SH",
    "weight_000905SH",
    "weight_000852SH",
})
FACTOR_STOCK_POOL_NODE = "stock_pool_member"
FACTOR_ALL_MARKET_STOCK_POOL_NODE = {
    "type": "DIRECT",
    "op": "nullary.true",
    "fields": {},
    "params": {},
}
FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES = {
    FACTOR_STOCK_POOL_NODE: FACTOR_ALL_MARKET_STOCK_POOL_NODE,
}
JSON_DSL_SOURCE_MAX_LENGTH = 1_000_000
PYTHON_DSL_SOURCE_MAX_LENGTH = 100_000


class DslDocument(BaseModel):
    """The three JSON fields that make up a Factor Query DSL document."""

    model_config = ConfigDict(extra="forbid", strict=True)

    factors: list[str] = Field(default_factory=list)
    derivatives: dict[str, Any] = Field(default_factory=dict)
    filters: list[str] = Field(default_factory=list)


class DslSource(BaseModel):
    """Both editor sources plus the language selected for execution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    language: Literal["json", "python"] = Field(
        description="当前回显并用于执行的源码版本",
    )
    json_source: str = Field(
        max_length=JSON_DSL_SOURCE_MAX_LENGTH,
        description="独立保存的 JSON DSL 源码",
    )
    python_source: str = Field(
        max_length=PYTHON_DSL_SOURCE_MAX_LENGTH,
        description="独立保存的 Python DSL 源码",
    )

    @model_validator(mode="before")
    @classmethod
    def upgrade_single_source(cls, value: Any) -> Any:
        """Upgrade records written by the former single-source contract."""
        if not isinstance(value, dict) or "source" not in value:
            return value
        if "json_source" in value or "python_source" in value:
            return value

        language = value.get("language")
        source = value.get("source")
        if language not in {"json", "python"} or not isinstance(source, str):
            return value
        document = (
            compile_python_dsl(source)
            if language == "python"
            else compile_json_dsl(source)
        )
        upgraded = {key: item for key, item in value.items() if key != "source"}
        upgraded["json_source"] = (
            source
            if language == "json"
            else json.dumps(document, ensure_ascii=False, indent=2)
        )
        upgraded["python_source"] = (
            source
            if language == "python"
            else dsl_document_to_python(document)
        )
        return upgraded

    @property
    def active_source(self) -> str:
        return (
            self.python_source
            if self.language == "python"
            else self.json_source
        )


def dsl_document(query: FactorQuery | dict[str, Any]) -> dict[str, Any]:
    value = (
        query.model_dump(mode="json")
        if isinstance(query, FactorQuery)
        else query
    )
    return {
        "factors": value.get("factors", []),
        "derivatives": value.get("derivatives", {}),
        "filters": value.get("filters", []),
    }


def json_dsl_source(
    document: dict[str, Any],
    *,
    external_derivatives: dict[str, Any] | None = None,
) -> DslSource:
    return DslSource(
        language="json",
        json_source=json.dumps(document, ensure_ascii=False, indent=2),
        python_source=dsl_document_to_python(
            document,
            external_derivatives=external_derivatives,
        ),
    )


def compile_json_dsl(
    source: str,
    *,
    external_derivatives: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse and validate one JSON DSL source."""
    try:
        value = json.loads(source)
    except json.JSONDecodeError as error:
        raise PythonDslCompileError(
            f"JSON DSL 第 {error.lineno} 行第 {error.colno} 列：{error.msg}"
        ) from error
    try:
        document = DslDocument.model_validate(value)
        authored_derivatives = document.derivatives
        query = FactorQuery.model_validate({
            "start_date": "2000-01-01",
            "end_date": "2000-01-01",
            "lookback": "P0D",
            "codes": [],
            **document.model_dump(mode="json"),
            "derivatives": {
                **(external_derivatives or {}),
                **authored_derivatives,
            },
        })
    except ValueError as error:
        raise PythonDslCompileError(f"JSON DSL 结果无效：{error}") from error
    return {
        "factors": query.factors,
        "derivatives": {
            name: query.derivatives[name].model_dump(mode="json")
            for name in authored_derivatives
        },
        "filters": query.filters,
    }


def compile_dsl_source(
    source: DslSource,
    *,
    external_derivatives: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile only the editor source selected by ``language``."""
    if source.language == "python":
        return compile_python_dsl(
            source.active_source,
            external_derivatives=external_derivatives,
        )
    return compile_json_dsl(
        source.active_source,
        external_derivatives=external_derivatives,
    )


def compile_factor_dsl_source(source: DslSource) -> dict[str, Any]:
    """Compile a Factor editor source with its managed stock-pool symbol."""
    return compile_dsl_source(
        source,
        external_derivatives=FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES,
    )


def dsl_document_to_python(
    document: dict[str, Any],
    *,
    external_derivatives: dict[str, Any] | None = None,
) -> str:
    """Render validated JSON DSL as an equivalent editable Python program."""
    validated = compile_json_dsl(
        json.dumps(document, ensure_ascii=False),
        external_derivatives=external_derivatives,
    )
    entries = list(validated["derivatives"].items())
    variables = {
        name: f"_dsl_{index}"
        for index, (name, _) in enumerate(entries)
    }
    declarations = [
        f"{variables[name]} = {_render_operation(node, name)}"
        for name, node in entries
    ]
    filters: list[str] = []
    for name in validated["filters"]:
        variable = variables.get(name)
        if variable is None:
            raise PythonDslCompileError(
                f"过滤器 {name!r} 没有对应派生算符"
            )
        filters.append(variable)
    return "\n\n".join([
        *declarations,
        f"FACTORS = {_python_literal(validated['factors'])}",
        "DERIVATIVES = ["
        + ", ".join(variables[name] for name, _ in entries)
        + "]",
        "FILTERS = [" + ", ".join(filters) + "]",
    ])


def _render_operation(node: dict[str, Any], name: str | None) -> str:
    category, member = node["op"].split(".", 1)
    python_member = f"{member}_" if keyword.iskeyword(member) else member
    operator = f"{node['type']}.{category}.{python_member}"
    arguments = [] if name is None else [_python_literal(name)]
    arguments.extend(
        f"{field}={_render_value(value)}"
        for field, value in node["fields"].items()
    )
    arguments.extend(
        f"{parameter}={_render_value(value)}"
        for parameter, value in node["params"].items()
    )
    if "on" in node:
        arguments.append(f"on={_render_value(node['on'])}")
    return f"{operator}({', '.join(arguments)})"


def _render_value(value: Any) -> str:
    if _is_derivative_node(value):
        return _render_operation(value, None)
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_python_literal(key)}: {_render_value(item)}"
            for key, item in value.items()
        ) + "}"
    return _python_literal(value)


def _python_literal(value: Any) -> str:
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_python_literal(key)}: {_python_literal(item)}"
            for key, item in value.items()
        ) + "}"
    raise PythonDslCompileError("DSL 包含无法转换为 Python 的值")


def _is_derivative_node(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") in {"DIRECT", "TS", "CS"}
        and isinstance(value.get("op"), str)
        and isinstance(value.get("fields"), dict)
        and isinstance(value.get("params"), dict)
    )


class FactorQueryRequest(BaseModel):
    """A FactorQuery plus both editor sources and the selected language."""

    model_config = ConfigDict(extra="forbid", strict=True)
    validation_derivatives: ClassVar[dict[str, Any]] = {}

    start_date: str
    end_date: str
    lookback: str | timedelta = "P0D"
    codes: list[str]
    factors: list[str] = Field(default_factory=list)
    derivatives: dict[str, Any] = Field(default_factory=dict)
    filters: list[str] = Field(default_factory=list)
    dsl_source: DslSource | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_query_and_source(self) -> "FactorQueryRequest":
        if self.dsl_source is None:
            self.runtime_query({
                "factors": self.factors,
                "derivatives": {
                    **self.validation_derivatives,
                    **self.derivatives,
                },
                "filters": self.filters,
            })
        else:
            compile_dsl_source(
                self.dsl_source,
                external_derivatives=self.validation_derivatives,
            )
        return self

    def compiled_document(self) -> dict[str, Any]:
        if self.dsl_source is not None:
            return compile_dsl_source(
                self.dsl_source,
                external_derivatives=self.validation_derivatives,
            )
        return {
            "factors": self.factors,
            "derivatives": self.derivatives,
            "filters": self.filters,
        }

    def runtime_query(
        self,
        document: dict[str, Any] | None = None,
    ) -> FactorQuery:
        return FactorQuery.model_validate({
            "start_date": self.start_date,
            "end_date": self.end_date,
            "lookback": self.lookback,
            "codes": self.codes,
            **(document if document is not None else self.compiled_document()),
        })

    def stored_json(
        self,
        runtime_query: FactorQuery | None = None,
        *,
        default_document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = runtime_query or self.runtime_query()
        result = query.model_dump(mode="json")
        source = self.dsl_source or json_dsl_source(
            default_document or dsl_document(query),
            external_derivatives=self.validation_derivatives,
        )
        result["dsl_source"] = source.model_dump(mode="json")
        return result


class QueryApplicationRequest(FactorQueryRequest):
    """Source-aware request accepted by the Query application."""

    def runtime_payload(self) -> dict[str, Any]:
        return {"dataset_query": self.runtime_query().model_dump(mode="json")}

    def stored_payload(self) -> dict[str, Any]:
        return {"dataset_query": self.stored_json()}


class FactorDatasetQueryRequest(FactorQueryRequest):
    """Factor editor query with a Backend-managed stock-pool dependency."""

    validation_derivatives: ClassVar[dict[str, Any]] = (
        FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES
    )


class FactorAnalysisApplicationRequest(BaseModel):
    """Source-aware request accepted by the Factor application."""

    model_config = ConfigDict(extra="forbid", strict=True)

    codes_query: FactorQueryRequest | None = None
    dataset_query: FactorDatasetQueryRequest
    factor_columns: list[str] = Field(min_length=1)
    return_columns: list[str] = Field(min_length=1)
    return_specs: dict[str, FactorReturnSpec]
    n_groups: int = Field(default=5, ge=2)
    n_select: int = Field(default=10, ge=1)
    preprocess: bool = True
    market_value_column: str = Field(default="circ_mv", min_length=1)

    @model_validator(mode="after")
    def validate_runtime_parameters(self) -> "FactorAnalysisApplicationRequest":
        self.runtime_parameters()
        return self

    def _dataset_runtime_query(
        self,
        codes_query: FactorQuery | None,
    ) -> FactorQuery:
        # The editor owns only factor logic. Return columns and the daily stock
        # pool filter are managed independently and are restored for Runtime.
        compiled = factor_editor_document(
            {"dataset_query": self.dataset_query.compiled_document()},
            self.return_columns,
        )
        generated_returns = {
            name: node
            for name, node in self.dataset_query.derivatives.items()
            if name in self.return_columns or re.fullmatch(r"ret\d+", name)
        }
        stock_pool = factor_stock_pool_node(codes_query)
        derivatives = {
            **(
                {FACTOR_STOCK_POOL_NODE: stock_pool}
                if stock_pool is not None
                else {}
            ),
            **compiled["derivatives"],
            **generated_returns,
        }
        filters = [
            *(
                [FACTOR_STOCK_POOL_NODE]
                if codes_query is not None and stock_pool is not None
                else []
            ),
            *compiled["filters"],
        ]
        return self.dataset_query.runtime_query({
            "factors": compiled["factors"],
            "derivatives": derivatives,
            "filters": filters,
        })

    def runtime_parameters(self) -> FactorAnalysisParameters:
        codes_query = (
            self.codes_query.runtime_query()
            if self.codes_query is not None
            else None
        )
        return FactorAnalysisParameters.model_validate({
            "codes_query": (
                codes_query.model_dump(mode="json")
                if codes_query is not None
                else None
            ),
            "dataset_query": self._dataset_runtime_query(
                codes_query,
            ).model_dump(mode="json"),
            "factor_columns": self.factor_columns,
            "return_columns": self.return_columns,
            "return_specs": {
                name: spec.model_dump(mode="json")
                for name, spec in self.return_specs.items()
            },
            "n_groups": self.n_groups,
            "n_select": self.n_select,
            "preprocess": self.preprocess,
            "market_value_column": self.market_value_column,
        })

    def runtime_payload(self) -> dict[str, Any]:
        return self.runtime_parameters().model_dump(mode="json")

    def stored_payload(self) -> dict[str, Any]:
        runtime = self.runtime_parameters()
        runtime_data = runtime.model_dump(mode="json")
        stored_dataset_query = without_factor_stock_pool(
            runtime.dataset_query,
        )
        dataset_document = factor_editor_document(
            {"dataset_query": stored_dataset_query.model_dump(mode="json")},
            self.return_columns,
        )
        dataset_request = self.dataset_query
        if dataset_request.dsl_source is not None:
            dataset_request = dataset_request.model_copy(update={
                "dsl_source": factor_dsl_source(
                    dataset_request.dsl_source,
                    self.return_columns,
                ),
            })
        return {
            **runtime_data,
            "codes_query": (
                self.codes_query.stored_json(runtime.codes_query)
                if self.codes_query is not None and runtime.codes_query is not None
                else None
            ),
            "dataset_query": dataset_request.stored_json(
                stored_dataset_query,
                default_document=dataset_document,
            ),
        }


def factor_editor_document(
    parameters: dict[str, Any],
    return_columns: list[str],
) -> dict[str, Any]:
    """Return the Factor editor's user-controlled DSL subset."""
    query = parameters["dataset_query"]
    return {
        "factors": [
            factor
            for factor in query["factors"]
            if factor not in FACTOR_MANAGED_COLUMNS
            and factor not in FACTOR_STOCK_POOL_FACTORS
        ],
        "derivatives": {
            name: node
            for name, node in query["derivatives"].items()
            if name != FACTOR_STOCK_POOL_NODE
            and name not in return_columns
            and re.fullmatch(r"ret\d+", name) is None
        },
        "filters": [
            name
            for name in query["filters"]
            if name != FACTOR_STOCK_POOL_NODE
        ],
    }


def factor_stock_pool_node(
    codes_query: FactorQuery | None,
) -> dict[str, Any] | None:
    """Return the managed membership node for the selected stock-pool type."""
    if codes_query is None:
        return FACTOR_ALL_MARKET_STOCK_POOL_NODE
    if FACTOR_STOCK_POOL_NODE not in codes_query.filters:
        return None
    member_node = codes_query.derivatives.get(FACTOR_STOCK_POOL_NODE)
    if member_node is None:
        return None
    member = member_node.model_dump(mode="json")
    fields = member.get("fields")
    right = fields.get("right") if isinstance(fields, dict) else None
    if (
        member.get("type") != "DIRECT"
        or member.get("op") != "binary.gt"
        or not isinstance(fields, dict)
        or fields.get("left") not in FACTOR_STOCK_POOL_FACTORS
        or isinstance(right, bool)
        or not isinstance(right, (int, float))
        or right != 0
        or member.get("params") != {}
    ):
        return None
    return member


def without_factor_stock_pool(query: FactorQuery) -> FactorQuery:
    """Remove Backend-managed stock-pool state from a stored dataset query."""
    return query.model_copy(update={
        "factors": [
            factor
            for factor in query.factors
            if factor not in FACTOR_STOCK_POOL_FACTORS
        ],
        "derivatives": {
            name: node
            for name, node in query.derivatives.items()
            if name != FACTOR_STOCK_POOL_NODE
        },
        "filters": [
            name
            for name in query.filters
            if name != FACTOR_STOCK_POOL_NODE
        ],
    })


def backtest_dataset_runtime_query(
    codes_query: FactorQuery | None,
    dataset_query: FactorQuery,
) -> FactorQuery:
    """Build the complete Backtest dataset query before Runtime submission."""
    member = None
    if (
        codes_query is not None
        and FACTOR_STOCK_POOL_NODE in codes_query.filters
    ):
        codes_member = codes_query.derivatives.get(FACTOR_STOCK_POOL_NODE)
        if codes_member is not None:
            member = codes_member.model_dump(mode="json")
    if member is None:
        dataset_member = dataset_query.derivatives.get(FACTOR_STOCK_POOL_NODE)
        if dataset_member is not None:
            member = dataset_member.model_dump(mode="json")
    if member is None:
        member = FACTOR_ALL_MARKET_STOCK_POOL_NODE

    derivatives = dict(dataset_query.derivatives)
    derivatives[FACTOR_STOCK_POOL_NODE] = member
    return dataset_query.model_copy(update={
        "factors": [
            factor
            for factor in dataset_query.factors
            if factor != FACTOR_STOCK_POOL_NODE
        ],
        "derivatives": derivatives,
    })


def factor_dsl_source(
    source: DslSource,
    return_columns: list[str],
) -> DslSource:
    """Remove former Backend-managed nodes from both saved editor sources."""
    values = source.model_dump(mode="python")
    for language, field in (
        ("json", "json_source"),
        ("python", "python_source"),
    ):
        try:
            document = (
                compile_json_dsl(
                    values[field],
                    external_derivatives=(
                        FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES
                    ),
                )
                if language == "json"
                else compile_python_dsl(
                    values[field],
                    external_derivatives=(
                        FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES
                    ),
                )
            )
        except PythonDslCompileError:
            # The inactive editor may contain an unfinished draft. It remains
            # untouched and will be validated only after the user selects it.
            continue
        authored = factor_editor_document(
            {"dataset_query": document},
            return_columns,
        )
        if authored == document:
            continue
        values[field] = (
            json.dumps(authored, ensure_ascii=False, indent=2)
            if language == "json"
            else dsl_document_to_python(
                authored,
                external_derivatives=(
                    FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES
                ),
            )
        )
    return DslSource.model_validate(values)


def upgrade_factor_dsl_sources(payload: dict[str, Any]) -> dict[str, Any]:
    """Prepare historical Factor parameters for the current editor contract."""
    upgraded = upgrade_dsl_sources(payload)
    dataset_query = upgraded.get("dataset_query")
    if not isinstance(dataset_query, dict):
        return with_historical_factor_return_specs(upgraded)
    factors = dataset_query.get("factors")
    derivatives = dataset_query.get("derivatives")
    filters = dataset_query.get("filters")
    if (
        not isinstance(factors, list)
        or not isinstance(derivatives, dict)
        or not isinstance(filters, list)
    ):
        return with_historical_factor_return_specs(upgraded)
    return_columns_value = upgraded.get("return_columns")
    return_columns = (
        return_columns_value
        if isinstance(return_columns_value, list)
        and all(isinstance(name, str) for name in return_columns_value)
        else []
    )
    stored_source = dataset_query.get("dsl_source")
    source = (
        factor_dsl_source(
            DslSource.model_validate(stored_source),
            return_columns,
        ).model_dump(mode="json")
        if isinstance(stored_source, dict)
        else None
    )
    result = dict(upgraded)
    result["dataset_query"] = {
        **dataset_query,
        "factors": [
            factor
            for factor in factors
            if factor not in FACTOR_STOCK_POOL_FACTORS
        ],
        "derivatives": {
            name: node
            for name, node in derivatives.items()
            if name != FACTOR_STOCK_POOL_NODE
        },
        "filters": [
            name
            for name in filters
            if name != FACTOR_STOCK_POOL_NODE
        ],
        **({"dsl_source": source} if source is not None else {}),
    }
    return with_historical_factor_return_specs(result)


def with_historical_factor_return_specs(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Infer missing return contracts from the persisted DSL without mutation."""
    if "return_specs" in payload:
        return payload
    return_columns = payload.get("return_columns")
    dataset_query = payload.get("dataset_query")
    derivatives = (
        dataset_query.get("derivatives")
        if isinstance(dataset_query, dict)
        else None
    )
    if (
        not isinstance(return_columns, list)
        or not return_columns
        or not all(isinstance(name, str) and name for name in return_columns)
        or not isinstance(derivatives, dict)
    ):
        return payload

    specs: dict[str, dict[str, Any]] = {}
    for column in return_columns:
        spec = historical_factor_return_spec(
            column,
            derivatives,
            frozenset(),
        )
        if spec is None:
            return payload
        specs[column] = spec
    return {**payload, "return_specs": specs}


def historical_factor_return_spec(
    reference: Any,
    derivatives: dict[str, Any],
    visited: frozenset[str],
) -> dict[str, Any] | None:
    """Resolve one historical return expression through named derivatives."""
    resolved = resolve_historical_derivative(reference, derivatives, visited)
    if resolved is None:
        return None
    node, next_visited = resolved
    operation = node.get("op")
    fields = node.get("fields")
    params = node.get("params")
    if operation == "unary.pct_change" and isinstance(params, dict):
        periods = params.get("periods")
        if (
            isinstance(periods, int)
            and not isinstance(periods, bool)
            and periods != 0
        ):
            return {"kind": "simple", "periods": abs(periods)}
        return None
    if operation == "unary.log_return" and isinstance(params, dict):
        periods = params.get("periods")
        if (
            isinstance(periods, int)
            and not isinstance(periods, bool)
            and periods != 0
        ):
            return {"kind": "log", "periods": abs(periods)}
        return None
    if operation == "unary.log" and isinstance(fields, dict):
        periods = historical_factor_return_periods(
            fields.get("col"),
            derivatives,
            next_visited,
        )
        return (
            {"kind": "log", "periods": periods}
            if periods is not None
            else None
        )
    if operation == "unary.shift" and isinstance(fields, dict):
        return historical_factor_return_spec(
            fields.get("col"),
            derivatives,
            next_visited,
        )
    return None


def historical_factor_return_periods(
    reference: Any,
    derivatives: dict[str, Any],
    visited: frozenset[str],
) -> int | None:
    resolved = resolve_historical_derivative(
        reference,
        derivatives,
        visited,
    )
    if resolved is None:
        return None
    node, next_visited = resolved
    if node.get("op") != "binary.div":
        return None
    fields = node.get("fields")
    if not isinstance(fields, dict):
        return None
    left = historical_shifted_source(
        fields.get("left"),
        derivatives,
        next_visited,
    )
    right = historical_shifted_source(
        fields.get("right"),
        derivatives,
        next_visited,
    )
    if left is None or right is None or left[0] != right[0]:
        return None
    distance = abs(left[1] - right[1])
    return distance or None


def historical_shifted_source(
    reference: Any,
    derivatives: dict[str, Any],
    visited: frozenset[str],
) -> tuple[str, int] | None:
    if isinstance(reference, str):
        if reference not in derivatives:
            return reference, 0
        resolved = resolve_historical_derivative(
            reference,
            derivatives,
            visited,
        )
        if resolved is None:
            return None
        node, next_visited = resolved
    elif isinstance(reference, dict):
        node = reference
        next_visited = visited
    else:
        return None

    if node.get("op") != "unary.shift":
        return json.dumps(node, ensure_ascii=False, sort_keys=True), 0
    fields = node.get("fields")
    params = node.get("params")
    periods = params.get("periods") if isinstance(params, dict) else None
    if (
        not isinstance(fields, dict)
        or not isinstance(periods, int)
        or isinstance(periods, bool)
    ):
        return None
    source = historical_shifted_source(
        fields.get("col"),
        derivatives,
        next_visited,
    )
    return None if source is None else (source[0], source[1] + periods)


def resolve_historical_derivative(
    reference: Any,
    derivatives: dict[str, Any],
    visited: frozenset[str],
) -> tuple[dict[str, Any], frozenset[str]] | None:
    if isinstance(reference, dict):
        return reference, visited
    if (
        not isinstance(reference, str)
        or reference not in derivatives
        or reference in visited
    ):
        return None
    node = derivatives[reference]
    if not isinstance(node, dict):
        return None
    return node, visited | {reference}


def compile_application_payload(
    application: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return the Runtime payload without mutating the stored business request."""
    if (
        application == "factor"
        and "dataset_query" in payload
        and "factor_columns" in payload
        and "return_columns" in payload
    ):
        prepared = with_historical_factor_return_specs(payload)
        if "return_specs" in prepared:
            return FactorAnalysisApplicationRequest.model_validate(
                prepared
            ).runtime_payload()
    if (
        application == "backtest"
        and "dataset_query" in payload
        and "callbacks" in payload
    ):
        return BacktestApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    if not contains_dsl_source(payload):
        return payload
    if application == "query":
        query = QueryApplicationRequest.model_validate(payload["dataset_query"])
        return query.runtime_payload()
    if application == "factor":
        return FactorAnalysisApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    return payload


def contains_dsl_source(value: Any) -> bool:
    if isinstance(value, dict):
        return "dsl_source" in value or any(
            contains_dsl_source(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_dsl_source(item) for item in value)
    return False


def upgrade_dsl_sources(value: Any) -> Any:
    """Expand legacy sources only in the two documented FactorQuery slots."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for key in ("codes_query", "dataset_query"):
        query = result.get(key)
        if not isinstance(query, dict) or "dsl_source" not in query:
            continue
        result[key] = {
            **query,
            "dsl_source": DslSource.model_validate(
                query["dsl_source"]
            ).model_dump(mode="json"),
        }
    return result


class BacktestApplicationRequest(BaseModel):
    """Source-aware request accepted by the Backtest application."""

    model_config = ConfigDict(extra="forbid", strict=True)

    config: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    codes_query: FactorQueryRequest | None = None
    dataset_query: FactorQueryRequest
    adj: Literal["hfq", "qfq"] | None = None
    annual_trading_days: int = Field(default=250, ge=1)
    risk_free_rate: float = Field(default=0.04, allow_inf_nan=False)
    utils: str = ""
    callbacks: dict[str, str]

    @model_validator(mode="after")
    def validate_runtime_parameters(self) -> "BacktestApplicationRequest":
        self.runtime_parameters()
        return self

    def runtime_parameters(self) -> BacktestParameters:
        codes_query = (
            self.codes_query.runtime_query()
            if self.codes_query is not None
            else None
        )
        dataset_query = backtest_dataset_runtime_query(
            codes_query,
            self.dataset_query.runtime_query(),
        )
        return BacktestParameters.model_validate({
            "config": self.config,
            "params": self.params,
            "codes_query": (
                codes_query.model_dump(mode="json")
                if codes_query is not None
                else None
            ),
            "dataset_query": dataset_query.model_dump(mode="json"),
            "adj": self.adj,
            "annual_trading_days": self.annual_trading_days,
            "risk_free_rate": self.risk_free_rate,
            "utils": self.utils,
            "callbacks": self.callbacks,
        })

    def runtime_payload(self) -> dict[str, Any]:
        return self.runtime_parameters().model_dump(mode="json")

    def stored_payload(self) -> dict[str, Any]:
        runtime = self.runtime_parameters()
        runtime_data = runtime.model_dump(mode="json")
        stored_dataset_query = self.dataset_query.runtime_query()
        return {
            **runtime_data,
            "codes_query": (
                self.codes_query.stored_json(runtime.codes_query)
                if self.codes_query is not None and runtime.codes_query is not None
                else None
            ),
            "dataset_query": self.dataset_query.stored_json(
                stored_dataset_query
            ),
        }


__all__ = [
    "BacktestApplicationRequest",
    "DslDocument",
    "DslSource",
    "FACTOR_STOCK_POOL_FACTORS",
    "FactorAnalysisApplicationRequest",
    "FactorQueryRequest",
    "QueryApplicationRequest",
    "compile_application_payload",
    "compile_dsl_source",
    "compile_factor_dsl_source",
    "factor_dsl_source",
    "upgrade_factor_dsl_sources",
    "upgrade_dsl_sources",
    "with_historical_factor_return_specs",
]
