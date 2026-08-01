import json

import pytest

from scheduler.errors import JobValidationError
from scheduler.jobs import SharedJobStore


def test_create_query_job_uses_job_scoped_shared_paths(tmp_path):
    store = SharedJobStore(tmp_path)

    metadata = store.create(
        "query",
        {"dataset_query": {"start_date": "2025-01-01"}, "output": ["data"]},
    )

    input_file = tmp_path / "query" / metadata["job_id"] / "input.json"
    output_dir = tmp_path / "query" / metadata["job_id"] / "output"
    assert input_file.is_file()
    assert output_dir.is_dir()
    assert json.loads(input_file.read_text(encoding="utf-8")) == {
        "dataset_query": {"start_date": "2025-01-01"},
        "output_dir": "output",
    }
    assert store.load(metadata["job_id"])["state"] == "CREATED"
    assert metadata["requested_outputs"] == ["data"]


def test_create_job_rejects_caller_output_dir(tmp_path):
    store = SharedJobStore(tmp_path)

    with pytest.raises(JobValidationError, match="output_dir"):
        store.create(
            "query",
            {
                "dataset_query": {"start_date": "2025-01-01"},
                "output": ["data"],
                "output_dir": "../outside",
            },
        )


def test_backtest_job_requires_callbacks(tmp_path):
    store = SharedJobStore(tmp_path)

    with pytest.raises(JobValidationError, match="callbacks"):
        store.create(
            "backtest",
            {"dataset_query": {"start_date": "2025-01-01"}, "output": ["return_summary"]},
        )


def test_create_factor_job_requires_analysis_columns(tmp_path):
    store = SharedJobStore(tmp_path)

    with pytest.raises(
        JobValidationError,
        match="factor_columns.*return_columns",
    ):
        store.create(
            "factor",
            {"dataset_query": {"start_date": "2025-01-01"}, "output": ["processed_data"]},
        )


def test_create_factor_job_uses_factor_shared_directory(tmp_path):
    store = SharedJobStore(tmp_path)

    metadata = store.create(
        "factor",
        {
            "dataset_query": {"start_date": "2025-01-01"},
            "factor_columns": ["close"],
            "return_columns": ["pct_chg"],
            "output": ["processed_data", "information_coefficient"],
        },
    )

    input_file = (
        tmp_path / "factor" / metadata["job_id"] / "input.json"
    )
    assert input_file.is_file()
    assert json.loads(input_file.read_text(encoding="utf-8")) == {
        "dataset_query": {"start_date": "2025-01-01"},
        "factor_columns": ["close"],
        "return_columns": ["pct_chg"],
        "output_dir": "output",
    }


def test_create_job_requires_output(tmp_path):
    store = SharedJobStore(tmp_path)

    with pytest.raises(JobValidationError, match="output"):
        store.create("query", {"dataset_query": {"start_date": "2025-01-01"}})


@pytest.mark.parametrize("output", [[], "data", ["data", "data"], ["unknown"]])
def test_create_job_validates_outputs(tmp_path, output):
    store = SharedJobStore(tmp_path)

    with pytest.raises(JobValidationError, match="output|名称|不支持"):
        store.create("query", {"dataset_query": {"start_date": "2025-01-01"}, "output": output})
