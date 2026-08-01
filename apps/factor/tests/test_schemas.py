import pytest
from pydantic import ValidationError

from apps.factor.schemas import FactorTaskCreate


def test_factor_request_rejects_overlapping_column_roles():
    with pytest.raises(ValidationError, match="不能重叠"):
        FactorTaskCreate.model_validate({
            "dataset_query": {
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "codes": ["000001.SZ"],
                "factors": ["close"],
            },
            "factor_columns": ["close"],
            "return_columns": ["close"],
            "output": ["processed_data"],
        })

