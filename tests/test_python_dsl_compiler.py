"""Tests for declarative Python Factor Query DSL compilation."""

import ast
import json

import pytest

from core.utils.dsl import PythonDslCompileError, compile_python_dsl


def test_compile_python_dsl_builds_named_dependencies() -> None:
    result = compile_python_dsl(
        '''
spread = DIRECT.binary.sub("price_spread", left="close", right="open")
spread_mean = TS.unary.rolling_mean(
    "price_spread_mean_20d",
    col=spread,
    window=20,
)
spread_rank = CS.unary.rank_pct("price_spread_rank", col=spread_mean)
selected = DIRECT.binary.gt("selected", left=spread_rank, right=0.8)

FACTORS: list[str] = ["open", "close"]
FILTERS: list = [selected]
'''
    )

    assert result["factors"] == ["open", "close"]
    assert list(result["derivatives"]) == [
        "price_spread",
        "price_spread_mean_20d",
        "price_spread_rank",
        "selected",
    ]
    assert result["derivatives"]["price_spread_mean_20d"]["fields"] == {
        "col": "price_spread"
    }
    assert result["derivatives"]["selected"]["fields"]["left"] == (
        "price_spread_rank"
    )
    assert result["filters"] == ["selected"]
    json.dumps(result, ensure_ascii=False)


def test_compile_python_dsl_uses_exact_hierarchical_operators() -> None:
    result = compile_python_dsl(
        '''
binary = DIRECT.binary.add("binary", left="open", right="close")
multiary = DIRECT.multiary.add("multiary", cols=["open", "high", "low", "close"])
market_mean = CS.unary.mean("market_mean", col="close")
industry_mean = CS.grouped.mean("industry_mean", col="close", by="industry")
FACTORS = ["close"]
FILTERS = []
'''
    )

    assert result["derivatives"]["binary"]["op"] == "binary.add"
    assert result["derivatives"]["multiary"]["op"] == "multiary.add"
    assert result["derivatives"]["market_mean"]["op"] == "unary.mean"
    assert result["derivatives"]["industry_mean"]["op"] == "grouped.mean"


def test_compile_python_dsl_keeps_unnamed_operations_nested() -> None:
    result = compile_python_dsl(
        '''
valid = DIRECT.multiary.and_(
    "valid",
    cols=[
        DIRECT.binary.gt(left="close", right=0),
        DIRECT.binary.gt(left="open", right=0),
    ],
)
FACTORS = []
FILTERS = [valid]
'''
    )

    assert list(result["derivatives"]) == ["valid"]
    assert result["derivatives"]["valid"]["fields"]["cols"][0]["op"] == (
        "binary.gt"
    )


def test_compile_python_dsl_supports_bounded_python_composition() -> None:
    result = compile_python_dsl(
        '''
periods = [1, 2, 3]
lags = [
    TS.unary.shift(f"close_lag_{period:02d}", col="close", periods=period)
    for period in periods
]

def positive(name, col):
    return DIRECT.binary.gt(name, left=col, right=0)

selected = positive("selected", lags[0])
FACTORS = ["close"]
FILTERS = [selected]
'''
    )

    assert list(result["derivatives"]) == [
        "close_lag_01",
        "close_lag_02",
        "close_lag_03",
        "selected",
    ]
    assert result["derivatives"]["selected"]["fields"]["left"] == (
        "close_lag_01"
    )


def test_compile_python_dsl_bounds_range_comprehensions() -> None:
    with pytest.raises(PythonDslCompileError, match="range 最多生成"):
        compile_python_dsl(
            '''
lags = [
    TS.unary.shift(f"lag_{period}", col="close", periods=period)
    for period in range(10001)
]
FACTORS = []
FILTERS = []
'''
        )


def test_compile_python_dsl_bounds_total_comprehension_work() -> None:
    source = "\n".join([
        *[
            f"unused_{batch} = [value for value in range(10000)]"
            for batch in range(4)
        ],
        "FACTORS = ['close']",
        "FILTERS = []",
    ])

    with pytest.raises(PythonDslCompileError, match="累计生成"):
        compile_python_dsl(source)


def test_compile_python_dsl_bounds_starred_expansion() -> None:
    with pytest.raises(PythonDslCompileError, match="累计生成"):
        compile_python_dsl(
            '''
base = [value for value in range(10000)]
expanded = [*base, *base, *base]
FACTORS = ["close"]
FILTERS = []
'''
        )


def test_compile_python_dsl_accepts_large_generated_programs() -> None:
    source = "\n".join([
        "generated_columns = [" + ", ".join(["'close'"] * 6_000) + "]",
        "FACTORS = ['close']",
        "FILTERS = []",
    ])

    assert sum(1 for _ in ast.walk(ast.parse(source))) > 5_000
    assert compile_python_dsl(source) == {
        "factors": ["close"],
        "derivatives": {},
        "filters": [],
    }


@pytest.mark.parametrize("missing", ["FACTORS", "FILTERS"])
def test_compile_python_dsl_requires_all_result_variables(missing: str) -> None:
    declarations = {
        "FACTORS": "FACTORS = ['close']",
        "FILTERS": "FILTERS = []",
    }
    source = "\n".join(
        declaration
        for name, declaration in declarations.items()
        if name != missing
    )

    with pytest.raises(PythonDslCompileError, match=missing):
        compile_python_dsl(source)


def test_compile_python_dsl_outputs_unreferenced_named_operations() -> None:
    result = compile_python_dsl(
        '''
unused = DIRECT.binary.add("still_output", left="close", right=1)
FACTORS = ["close"]
FILTERS = []
'''
    )

    assert list(result["derivatives"]) == ["still_output"]


def test_compile_python_dsl_outputs_standalone_named_operations() -> None:
    result = compile_python_dsl(
        '''
DIRECT.binary.add("standalone", left="close", right=1)
FACTORS = []
FILTERS = []
'''
    )

    assert list(result["derivatives"]) == ["standalone"]


def test_compile_python_dsl_does_not_share_named_operations_between_calls() -> None:
    compile_python_dsl(
        '''
value = DIRECT.binary.add("first_request", left="close", right=1)
FACTORS = []
FILTERS = []
'''
    )

    result = compile_python_dsl(
        '''
FACTORS = ["close"]
FILTERS = []
'''
    )

    assert result["derivatives"] == {}


def test_compile_python_dsl_rejects_non_boolean_filter() -> None:
    with pytest.raises(PythonDslCompileError, match="必须返回 BOOL"):
        compile_python_dsl(
            '''
mean = TS.unary.rolling_mean("mean", col="close", window=20)
FACTORS = ["close"]
FILTERS = [mean]
'''
        )


def test_compile_python_dsl_rejects_duplicate_operation_names() -> None:
    with pytest.raises(PythonDslCompileError, match="算符名称重复"):
        compile_python_dsl(
            '''
left = DIRECT.binary.sub("duplicate", left="close", right="open")
right = DIRECT.binary.add("duplicate", left="close", right="open")
FACTORS = ["close"]
FILTERS = []
'''
        )


def test_compile_python_dsl_rejects_arbitrary_python_statements() -> None:
    with pytest.raises(PythonDslCompileError, match="只允许变量声明、安全辅助函数"):
        compile_python_dsl(
            '''
import os
FACTORS = ["close"]
FILTERS = []
'''
        )


def test_compile_python_dsl_rejects_indirect_callable_parameters() -> None:
    with pytest.raises(PythonDslCompileError, match="不允许调用函数 'operation'"):
        compile_python_dsl(
            '''
def invoke(operation):
    return operation(operation)

invalid = invoke(invoke)
FACTORS = []
FILTERS = []
'''
        )


def test_compile_python_dsl_rejects_nested_comprehensions() -> None:
    with pytest.raises(PythonDslCompileError, match="不支持嵌套推导式"):
        compile_python_dsl(
            '''
lags = [
    [TS.unary.shift(f"lag_{left}_{right}", col="close", periods=right) for right in range(2)]
    for left in range(2)
]
FACTORS = []
FILTERS = []
'''
        )


def test_compile_python_dsl_bounds_format_width() -> None:
    with pytest.raises(PythonDslCompileError, match="宽度或精度过大"):
        compile_python_dsl(
            '''
name = f"factor_{1:1000000000d}"
FACTORS = [name]
FILTERS = []
'''
        )


def test_compile_python_dsl_rejects_flat_operator_names() -> None:
    with pytest.raises(PythonDslCompileError, match="分层 DSL 算符"):
        compile_python_dsl(
            '''
value = DIRECT.binary_add("value", left="close", right="open")
FACTORS = ["close", "open"]
FILTERS = []
'''
        )
