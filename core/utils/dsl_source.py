"""Source-aware DSL request models compiled before Runtime submission."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.apps.backtest.schema import BacktestParameters
from runtime.apps.factor.schema import (
    FACTOR_INDUSTRY_COLUMNS,
    FactorAnalysisParameters,
    FactorIndustryColumn,
    FactorReturnSpec,
)
from runtime.apps.optimization.schema import (
    OptimizationParameters,
    OptimizationSettings,
)
from runtime.apps.query.dsl.query import derivative_references
from runtime.apps.query.schema import FactorQuery
from runtime.apps.sensitivity.schema import (
    SensitivityParameters,
    SensitivitySettings,
)

from core.utils.dsl import PythonDslCompileError, compile_python_dsl, dsl_catalog


FACTOR_MANAGED_COLUMNS = frozenset({
    "circ_mv",
    "total_mv",
    *FACTOR_INDUSTRY_COLUMNS,
})
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

    factors: list[str]
    derivatives: dict[str, Any]
    filters: list[str]


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

    @property
    def active_source(self) -> str:
        return (
            self.python_source
            if self.language == "python"
            else self.json_source
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
    available_factors = dsl_catalog().factors
    if source.language == "python":
        document = compile_python_dsl(
            source.active_source,
            external_derivatives=external_derivatives,
            available_factors=available_factors,
        )
    else:
        document = compile_json_dsl(
            source.active_source,
            external_derivatives=external_derivatives,
        )
    _validate_source_fields(
        document,
        external_derivatives,
        available_factors,
    )
    return document


def _validate_source_fields(
    document: dict[str, Any],
    external_derivatives: dict[str, Any] | None,
    available_factors: list[str],
) -> None:
    try:
        query = FactorQuery.model_validate({
            "start_date": "2000-01-01",
            "end_date": "2000-01-01",
            "lookback": "P0D",
            "codes": [],
            **document,
            "derivatives": {
                **(external_derivatives or {}),
                **document["derivatives"],
            },
        })
    except ValueError as error:
        raise PythonDslCompileError(f"DSL 结果无效：{error}") from error
    unknown = sorted(set(query.source_factors()) - set(available_factors))
    if unknown:
        raise PythonDslCompileError(
            f"DSL 引用了不存在的数据字段：{unknown}"
        )


def compile_factor_dsl_source(source: DslSource) -> dict[str, Any]:
    """Compile a Factor editor source with its managed stock-pool symbol."""
    document = compile_dsl_source(
        source,
        external_derivatives=FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES,
    )
    try:
        validate_factor_editor_document(document, factor_columns=[], return_columns=[])
    except ValueError as error:
        raise PythonDslCompileError(str(error)) from error
    return document


def compile_backtest_dsl_source(source: DslSource) -> dict[str, Any]:
    """Compile a Backtest dataset source and enforce its reserved names."""
    document = compile_dsl_source(
        source,
        external_derivatives=FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES,
    )
    try:
        validate_backtest_editor_document(document)
    except ValueError as error:
        raise PythonDslCompileError(str(error)) from error
    return document


class FactorQueryRequest(BaseModel):
    """A FactorQuery plus both editor sources and the selected language."""

    model_config = ConfigDict(extra="forbid", strict=True)
    validation_derivatives: ClassVar[dict[str, Any]] = {}

    start_date: str
    end_date: str
    lookback: str | timedelta
    codes: list[str]
    factors: list[str]
    derivatives: dict[str, Any]
    filters: list[str]
    dsl_source: DslSource

    @model_validator(mode="after")
    def validate_query_and_source(self) -> "FactorQueryRequest":
        self.runtime_query()
        return self

    def compiled_document(self) -> dict[str, Any]:
        return compile_dsl_source(
            self.dsl_source,
            external_derivatives=self.validation_derivatives,
        )

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

    def stored_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class QueryApplicationRequest(FactorQueryRequest):
    """Source-aware request accepted by the Query application."""

    def runtime_payload(self) -> dict[str, Any]:
        return {"dataset_query": self.runtime_query().model_dump(mode="json")}

    def stored_payload(self) -> dict[str, Any]:
        return {"dataset_query": self.stored_json()}


class ManagedDatasetQueryRequest(FactorQueryRequest):
    """Dataset query allowed to reference the Backend-managed stock-pool node."""

    validation_derivatives: ClassVar[dict[str, Any]] = (
        FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES
    )

    @model_validator(mode="after")
    def validate_query_and_source(self) -> "ManagedDatasetQueryRequest":
        self.compiled_document()
        return self


class FactorAnalysisApplicationRequest(BaseModel):
    """Source-aware request accepted by the Factor application."""

    model_config = ConfigDict(extra="forbid", strict=True)

    codes_query: FactorQueryRequest | None
    dataset_query: ManagedDatasetQueryRequest
    factor_columns: list[str] = Field(min_length=1)
    return_columns: list[str] = Field(min_length=1)
    return_specs: dict[str, FactorReturnSpec]
    n_groups: int = Field(ge=2)
    n_select: int = Field(ge=1)
    preprocess: bool
    market_value_column: str = Field(min_length=1)
    industry_column: FactorIndustryColumn

    @model_validator(mode="after")
    def validate_runtime_parameters(self) -> "FactorAnalysisApplicationRequest":
        self.runtime_parameters()
        return self

    def _dataset_runtime_query(
        self,
        codes_query: FactorQuery | None,
    ) -> FactorQuery:
        if self.dataset_query.codes:
            raise ValueError("Factor 托管股票池要求 dataset_query.codes=[]；不能传入会被候选池覆盖的静态代码")
        compiled = self.dataset_query.compiled_document()
        validate_factor_editor_document(
            compiled,
            self.factor_columns,
            self.return_columns,
        )
        generated_returns: dict[str, Any] = {}
        for name in self.return_columns:
            node = self.dataset_query.derivatives.get(name)
            if node is None:
                raise ValueError(
                    f"dataset_query.derivatives 缺少收益率列定义：{name!r}"
                )
            generated_returns[name] = node
        stock_pool = factor_stock_pool_node(codes_query)
        derivatives = {
            FACTOR_STOCK_POOL_NODE: stock_pool,
            **compiled["derivatives"],
            **generated_returns,
        }
        factors = list(compiled["factors"])
        outputs = set(factors) | set(derivatives)
        required = [
            *self.factor_columns,
            *self.return_columns,
            self.market_value_column,
        ]
        if self.preprocess and self.industry_column != "industry":
            required.append(self.industry_column)
        for name in required:
            if name not in outputs:
                factors.append(name)
                outputs.add(name)
        filters = [
            *(
                [FACTOR_STOCK_POOL_NODE]
                if codes_query is not None
                else []
            ),
            *compiled["filters"],
        ]
        return self.dataset_query.runtime_query({
            "factors": factors,
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
            "industry_column": self.industry_column,
        })

    def runtime_payload(self) -> dict[str, Any]:
        return self.runtime_parameters().model_dump(mode="json")

    def stored_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def validate_factor_editor_document(
    document: dict[str, Any],
    factor_columns: list[str],
    return_columns: list[str],
) -> None:
    """Reject Backend-owned output names in the user-authored Factor source."""
    generated_return_names = {
        name
        for name in document["derivatives"]
        if re.fullmatch(r"ret\d+", name)
    }
    reserved = {
        *FACTOR_MANAGED_COLUMNS,
        *FACTOR_STOCK_POOL_FACTORS,
        FACTOR_STOCK_POOL_NODE,
        *return_columns,
        *generated_return_names,
    }
    outputs = set(document["factors"]) | set(document["derivatives"])
    if conflicts := sorted(outputs & reserved):
        raise ValueError(
            "Factor DSL 不能定义由 Backend 注入的保留列："
            f"{conflicts}"
        )
    if conflicts := sorted(set(document["filters"]) & reserved):
        raise ValueError(
            "Factor DSL 不能过滤由 Backend 注入的保留列："
            f"{conflicts}"
        )
    missing_factors = sorted(set(factor_columns) - outputs)
    if missing_factors:
        raise ValueError(
            "factor_columns 必须由活动 Factor DSL 输出："
            f"{missing_factors}"
        )


def factor_stock_pool_node(
    codes_query: FactorQuery | None,
) -> dict[str, Any]:
    """Return the managed membership node for the selected stock-pool type."""
    if codes_query is None:
        return FACTOR_ALL_MARKET_STOCK_POOL_NODE
    if FACTOR_STOCK_POOL_NODE not in codes_query.filters:
        raise ValueError(
            "codes_query.filters 必须包含 stock_pool_member"
        )
    member_node = codes_query.derivatives.get(FACTOR_STOCK_POOL_NODE)
    if member_node is None:
        raise ValueError(
            "codes_query.derivatives 必须定义 stock_pool_member"
        )
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
        raise ValueError(
            "codes_query.derivatives.stock_pool_member 必须使用受支持指数权重"
            "构造 binary.gt(..., 0)"
        )
    return member


def validate_backtest_editor_document(document: dict[str, Any]) -> None:
    """Reject authored definitions and filters of the managed stock-pool node."""
    if (
        FACTOR_STOCK_POOL_NODE in document["factors"]
        or FACTOR_STOCK_POOL_NODE in document["derivatives"]
        or FACTOR_STOCK_POOL_NODE in document["filters"]
    ):
        raise ValueError(
            "Backtest dataset DSL 不能定义或过滤 Backend 注入的 "
            "stock_pool_member"
        )


def backtest_dataset_runtime_query(
    codes_query: FactorQuery | None,
    dataset_query: ManagedDatasetQueryRequest,
) -> FactorQuery:
    """Build the complete Backtest dataset query before Runtime submission."""
    document = dataset_query.compiled_document()
    validate_backtest_editor_document(document)
    stock_pool_derivatives = {
        FACTOR_STOCK_POOL_NODE: FACTOR_ALL_MARKET_STOCK_POOL_NODE,
    }
    if codes_query is not None:
        # FactorQuery has already checked that filters reference defined BOOL nodes.
        if FACTOR_STOCK_POOL_NODE not in codes_query.filters:
            raise ValueError("codes_query.filters 必须包含 stock_pool_member")
        required: set[str] = set()
        references: set[str] = set()
        pending = [FACTOR_STOCK_POOL_NODE]
        while pending:
            name = pending.pop()
            if name in required:
                continue
            required.add(name)
            inputs = derivative_references(codes_query.derivatives[name])[0]
            references.update(inputs)
            pending.extend((inputs & codes_query.derivatives.keys()) - required)
        stock_pool_derivatives = {
            name: node.model_dump(mode="json")
            for name, node in codes_query.derivatives.items()
            if name in required
        }
        conflicts = required & set(document["factors"])
        conflicts.update(
            name for name in required & document["derivatives"].keys()
            if document["derivatives"][name] != stock_pool_derivatives[name]
        )
        conflicts.update((references - required) & document["derivatives"].keys())
        if conflicts:
            raise ValueError(
                "Backtest dataset DSL 与 codes_query 股票池依赖定义冲突："
                f"{sorted(conflicts)}"
            )

    return dataset_query.runtime_query({
        "factors": document["factors"],
        "derivatives": {
            **stock_pool_derivatives,
            **document["derivatives"],
        },
        "filters": document["filters"],
    })


def compile_application_payload(
    application: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return the Runtime payload without mutating the stored business request."""
    if application == "query":
        if set(payload) != {"dataset_query"}:
            raise ValueError(
                "Query 工作流参数必须且只能包含 dataset_query"
            )
        return QueryApplicationRequest.model_validate(
            payload["dataset_query"]
        ).runtime_payload()
    if application == "factor":
        return FactorAnalysisApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    if application == "backtest":
        return BacktestApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    if application == "optimization":
        return OptimizationApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    if application == "sensitivity":
        return SensitivityApplicationRequest.model_validate(
            payload
        ).runtime_payload()
    if application == "incremental":
        return payload
    raise ValueError(f"不支持编译工作流参数：{application}")


class BacktestApplicationRequest(BaseModel):
    """Source-aware request accepted by the Backtest application."""

    model_config = ConfigDict(extra="forbid", strict=True)

    config: dict[str, Any]
    params: dict[str, Any]
    codes_query: FactorQueryRequest | None
    dataset_query: ManagedDatasetQueryRequest
    adj: Literal["hfq", "qfq"] | None
    annual_trading_days: int = Field(ge=1)
    risk_free_rate: float = Field(allow_inf_nan=False)
    utils: str
    callbacks: dict[str, str]

    @model_validator(mode="after")
    def validate_runtime_parameters(self) -> "BacktestApplicationRequest":
        required_config = {
            "cash",
            "commission",
            "tax",
            "enableMinimumPerTransactionFee",
        }
        if missing := sorted(required_config - set(self.config)):
            raise ValueError(
                "config 缺少必填基础配置："
                f"{missing}"
            )
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
            self.dataset_query,
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
        return self.model_dump(mode="json")


class OptimizationApplicationRequest(
    BacktestApplicationRequest,
    OptimizationSettings,
):
    """Source-preserving Backtest request plus optimization settings."""

    def runtime_parameters(self) -> OptimizationParameters:
        settings = {
            name: getattr(self, name)
            for name in OptimizationSettings.model_fields
        }
        return OptimizationParameters.model_validate({
            **super().runtime_parameters().model_dump(mode="json"),
            **settings,
        })


class SensitivityApplicationRequest(
    BacktestApplicationRequest,
    SensitivitySettings,
):
    """Source-preserving Backtest request plus sensitivity cases."""

    def runtime_parameters(self) -> SensitivityParameters:
        settings = {
            name: getattr(self, name)
            for name in SensitivitySettings.model_fields
        }
        return SensitivityParameters.model_validate({
            **super().runtime_parameters().model_dump(mode="json"),
            **settings,
        })


__all__ = [
    "BacktestApplicationRequest",
    "DslDocument",
    "DslSource",
    "FACTOR_STOCK_POOL_FACTORS",
    "FactorAnalysisApplicationRequest",
    "FactorQueryRequest",
    "OptimizationApplicationRequest",
    "QueryApplicationRequest",
    "SensitivityApplicationRequest",
    "compile_application_payload",
    "compile_backtest_dsl_source",
    "compile_dsl_source",
    "compile_factor_dsl_source",
]
