import numpy as np
import pandas as pd
import pytest

from core.mcp.views.dolphindb import serialize_dolphindb_result


def test_dolphindb_table_result_is_bounded_and_json_safe() -> None:
    result = serialize_dolphindb_result(
        pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [4, 5, 6], "c": [7, 8, 9]}),
        2,
    )

    assert result.kind == "table"
    assert (result.row_count, result.column_count, result.truncated) == (3, 3, True)
    assert result.columns == ["a", "b"]
    assert result.value == [{"a": 1.0, "b": 4}, {"a": None, "b": 5}]


def test_dolphindb_matrix_result_preserves_dimensions_and_labels() -> None:
    result = serialize_dolphindb_result(
        [np.arange(12).reshape(3, 4), np.array(["r1", "r2", "r3"]), np.array(["a", "b", "c", "d"])],
        2,
    )

    assert result.kind == "matrix"
    assert (result.row_count, result.column_count, result.truncated) == (3, 4, True)
    assert result.columns == ["a", "b"]
    assert result.value == {
        "data": [[0, 1], [4, 5]],
        "row_labels": ["r1", "r2"],
        "column_labels": ["a", "b"],
    }


def test_dolphindb_mapping_result_serializes_nested_table() -> None:
    result = serialize_dolphindb_result(
        {"version": "2.00", "plugins": pd.DataFrame({"name": ["Backtest"]})},
        10,
    )

    assert result.kind == "mapping"
    assert result.value == {"version": "2.00", "plugins": [{"name": "Backtest"}]}


def test_dolphindb_temporal_result_uses_iso_strings_and_null() -> None:
    result = serialize_dolphindb_result(
        pd.DataFrame({"time": [pd.Timestamp("2026-08-15 09:30:00"), pd.NaT]}),
        10,
    )
    vector = serialize_dolphindb_result(
        np.array(["2026-08-15T09:30:00", "NaT"], dtype="datetime64[ns]"),
        10,
    )

    assert result.value == [{"time": "2026-08-15T09:30:00"}, {"time": None}]
    assert vector.value == ["2026-08-15T09:30", None]
    assert serialize_dolphindb_result(pd.NaT, 10).kind == "null"


def test_dolphindb_scalar_result_has_character_budget() -> None:
    result = serialize_dolphindb_result("x" * 1_000_001, 1)

    assert result.truncated is True
    assert len(result.value) == 1_000_000


@pytest.mark.parametrize("size", [223, 224, 250, 300, 320])
@pytest.mark.parametrize("with_labels", [False, True])
def test_matrix_budget_exhaustion_preserves_result_envelope(size, with_labels) -> None:
    labels = np.arange(size) if with_labels else None
    result = serialize_dolphindb_result([np.ones((size, size)), labels, labels], 2000)

    assert result.kind == "matrix"
    assert (result.row_count, result.column_count) == (size, size)
    assert set(result.value) == {"data", "row_labels", "column_labels"}
    assert result.truncated is (size >= 224 or with_labels)
    result.model_dump_json()


def test_matrix_labels_and_data_share_character_budget() -> None:
    labels = np.array(["x" * 600_000, "y" * 600_000])
    result = serialize_dolphindb_result([np.ones((2, 2)), labels, labels], 2000)

    assert result.truncated
    assert result.kind == "matrix"
    assert set(result.value) == {"data", "row_labels", "column_labels"}
    assert result.columns == []


def test_matrix_single_row_preview_keeps_both_label_keys() -> None:
    result = serialize_dolphindb_result([np.ones((2, 2)), np.arange(2), np.arange(2)], 1)

    assert result.value == {"data": [[1.0]], "row_labels": [0], "column_labels": [0]}
