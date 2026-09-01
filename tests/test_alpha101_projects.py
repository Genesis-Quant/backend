"""Compile every Alpha101 formula as its own project 7 v2 analysis."""

from __future__ import annotations

from datetime import timedelta

import pytest

from alpha101_cases import (
    ALPHA101_EXPRESSIONS,
    PROJECT_7_V2_END_DATE,
    PROJECT_7_V2_LOOKBACK,
    PROJECT_7_V2_NEGATED_NUMBERS,
    PROJECT_7_V2_RETURN_COLUMNS,
    PROJECT_7_V2_START_DATE,
    alpha101_name,
    alpha101_project_title,
    compile_alpha101,
    project_7_v2_payload,
)
from core.utils.dsl_source import FactorAnalysisApplicationRequest


ALPHA_NUMBERS = tuple(range(1, 102))


def test_alpha101_defines_exactly_101_independent_projects() -> None:
    assert tuple(sorted(ALPHA101_EXPRESSIONS)) == ALPHA_NUMBERS
    assert len({alpha101_name(number) for number in ALPHA_NUMBERS}) == 101
    assert len({alpha101_project_title(number) for number in ALPHA_NUMBERS}) == 101


def test_project_7_v2_preserves_the_reviewed_factor_orientations() -> None:
    assert PROJECT_7_V2_NEGATED_NUMBERS == frozenset(
        {
            1,
            7,
            12,
            22,
            43,
            45,
            48,
            58,
            61,
            62,
            65,
            74,
            75,
            78,
            81,
            85,
            98,
            99,
            101,
        }
    )
    for number in ALPHA_NUMBERS:
        name = alpha101_name(number)
        payload = project_7_v2_payload(number)
        assert payload["dataset_query"]["derivatives"][name] == compile_alpha101(
            number,
            negate=number in PROJECT_7_V2_NEGATED_NUMBERS,
        )["derivatives"][name]


def test_alpha_029_outer_min_is_a_five_day_time_series_minimum() -> None:
    """The paper defines min(x, d) as ts_min, not element-wise min."""
    document = compile_alpha101(29)
    root = document["derivatives"][alpha101_name(29)]

    rolling_minimums: list[dict[str, object]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get("op") == "unary.rolling_min":
                rolling_minimums.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(root)

    assert {node["params"]["window"] for node in rolling_minimums} == {2, 5}


@pytest.mark.parametrize(
    "number",
    ALPHA_NUMBERS,
    ids=lambda number: f"alpha_{number:03d}",
)
def test_alpha101_python_dsl_compiles_for_its_project(number: int) -> None:
    name = alpha101_name(number)
    document = compile_alpha101(number)

    assert document["factors"] == []
    assert name in document["derivatives"]
    assert document["derivatives"][name]["op"] == "unary.cast"
    assert document["filters"] == ["tradable"]


@pytest.mark.parametrize(
    "number",
    ALPHA_NUMBERS,
    ids=lambda number: f"alpha_{number:03d}",
)
def test_alpha101_project_uses_project_7_v2_parameters(number: int) -> None:
    name = alpha101_name(number)
    request = FactorAnalysisApplicationRequest.model_validate(
        project_7_v2_payload(number)
    )
    runtime = request.runtime_parameters()

    assert runtime.codes_query is not None
    assert runtime.codes_query.start_date == PROJECT_7_V2_START_DATE
    assert runtime.codes_query.end_date == PROJECT_7_V2_END_DATE
    assert runtime.codes_query.filters == ["stock_pool_member"]
    assert runtime.codes_query.derivatives[
        "stock_pool_member"
    ].fields.left == "weight_000300SH"

    assert runtime.dataset_query.start_date == PROJECT_7_V2_START_DATE
    assert runtime.dataset_query.end_date == PROJECT_7_V2_END_DATE
    assert PROJECT_7_V2_LOOKBACK == "P400D"
    assert runtime.dataset_query.lookback == timedelta(days=400)
    assert runtime.dataset_query.codes == []
    assert runtime.dataset_query.filters == ["stock_pool_member", "tradable"]
    assert name in runtime.dataset_query.derivatives
    assert runtime.factor_columns == [name]
    assert runtime.return_columns == list(PROJECT_7_V2_RETURN_COLUMNS)
    assert all(
        runtime.return_specs[column].kind == "log"
        and runtime.return_specs[column].periods == 1
        for column in PROJECT_7_V2_RETURN_COLUMNS
    )
    assert runtime.n_groups == 10
    assert runtime.n_select == 10
    assert runtime.preprocess is True
    assert runtime.market_value_column == "circ_mv"
    assert runtime.industry_column == "industry_l0"
