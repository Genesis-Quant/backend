import pytest
from pydantic import ValidationError

from apps.query.schemas import QueryTaskCreate


def query_payload():
    return {
        "dataset_query": {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "codes": ["000001.SZ"],
            "factors": ["close"],
        },
        "output": ["data"],
    }


def test_query_request_rejects_invalid_date_range_and_duplicate_outputs():
    payload = query_payload()
    payload["dataset_query"]["start_date"] = "2025-02-01"
    payload["output"] = ["data", "data"]
    with pytest.raises(ValidationError):
        QueryTaskCreate.model_validate(payload)


def test_query_request_rejects_backend_output_dir():
    payload = query_payload()
    payload["output_dir"] = "outside"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        QueryTaskCreate.model_validate(payload)

