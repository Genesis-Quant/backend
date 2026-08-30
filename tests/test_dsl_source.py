import json

from core.apps.workflows.artifacts import workspace_input_file
from core.apps.workflows.models import WorkflowAttempt, WorkflowWorkspace
from core.apps.workflows.services import prepare_workspace
from config import ArenaSettings
from core.utils.dsl_source import (
    BacktestApplicationRequest,
    DslSource,
    FactorAnalysisApplicationRequest,
    QueryApplicationRequest,
    compile_application_payload,
    compile_dsl_source,
    compile_factor_dsl_source,
    factor_dsl_source,
    upgrade_dsl_sources,
)


PYTHON_DSL = """
momentum = TS.unary_pct_change(
    "momentum",
    col="close",
    periods=20,
)

FACTORS = ["close"]
DERIVATIVES = [momentum]
FILTERS = []
""".strip()

CALLBACKS = {
    "initialize": "def initialize(mutable context) { return NULL }",
    "beforeTrading": "def beforeTrading(mutable context) { return NULL }",
    "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
    "onSnapshot": "def onSnapshot(mutable context, message, indicator) { return NULL }",
    "onOrder": "def onOrder(mutable context, event) { return NULL }",
    "onTrade": "def onTrade(mutable context, event) { return NULL }",
    "afterTrading": "def afterTrading(mutable context) { return NULL }",
    "finalize": "def finalize(mutable context) { return NULL }",
}


def query_request() -> dict:
    json_source = """{
  "factors": ["vol"],
  "derivatives": {},
  "filters": []
}"""
    return {
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "lookback": "P30D",
        "codes": ["000001.SZ"],
        # The preview is deliberately stale. Backend must compile source.
        "factors": ["vol"],
        "derivatives": {},
        "filters": [],
        "dsl_source": {
            "language": "python",
            "json_source": json_source,
            "python_source": PYTHON_DSL,
        },
    }


def test_query_source_is_stored_and_python_is_replaced_for_runtime() -> None:
    request = QueryApplicationRequest.model_validate(query_request())
    stored = request.stored_payload()
    runtime = compile_application_payload("query", stored)

    assert stored["dataset_query"]["dsl_source"] == {
        "language": "python",
        "json_source": """{
  "factors": ["vol"],
  "derivatives": {},
  "filters": []
}""",
        "python_source": PYTHON_DSL,
    }
    assert runtime["dataset_query"]["factors"] == ["close"]
    assert set(runtime["dataset_query"]["derivatives"]) == {"momentum"}
    assert "dsl_source" not in runtime["dataset_query"]


def test_json_source_uses_the_same_submission_path() -> None:
    request = QueryApplicationRequest.model_validate({
        **query_request(),
        "dsl_source": {
            "language": "json",
            "json_source": """{
              "factors": ["open"],
              "derivatives": {},
              "filters": []
            }""",
            # An inactive source is stored verbatim and is not compiled.
            "python_source": "not valid Python DSL",
        },
    })
    stored = request.stored_payload()
    runtime = compile_application_payload("query", stored)

    assert stored["dataset_query"]["dsl_source"]["language"] == "json"
    assert stored["dataset_query"]["dsl_source"]["python_source"] == (
        "not valid Python DSL"
    )
    assert runtime["dataset_query"]["factors"] == ["open"]


def test_python_source_does_not_validate_inactive_json() -> None:
    request_data = query_request()
    request_data["dsl_source"]["json_source"] = "not valid JSON DSL"

    request = QueryApplicationRequest.model_validate(request_data)
    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert stored["dataset_query"]["dsl_source"]["json_source"] == (
        "not valid JSON DSL"
    )
    assert runtime["dataset_query"]["factors"] == ["close"]


def test_json_source_accepts_large_generated_factor_documents() -> None:
    factors = [f"generated_factor_{index}" for index in range(12_000)]
    json_source = json.dumps({
        "factors": factors,
        "derivatives": {},
        "filters": [],
    })

    assert len(json_source) > 100_000
    source = DslSource(
        language="json",
        json_source=json_source,
        python_source=PYTHON_DSL,
    )

    assert compile_dsl_source(source)["factors"] == factors


def test_factor_compiler_accepts_managed_stock_pool_references() -> None:
    source = DslSource(
        language="python",
        json_source="inactive JSON draft",
        python_source='''
rank = CS.rank_pct(
    "pool_rank",
    col="turnover_rate_f",
    on="stock_pool_member",
)
FACTORS = []
DERIVATIVES = [rank]
FILTERS = []
'''.strip(),
    )

    document = compile_factor_dsl_source(source)

    assert document["derivatives"]["pool_rank"]["on"] == (
        "stock_pool_member"
    )


def test_factor_python_source_keeps_backend_generated_return_nodes() -> None:
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": None,
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
        },
        "factor_columns": ["momentum"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": "circ_mv",
    })
    stored = request.stored_payload()
    runtime = compile_application_payload("factor", stored)

    assert stored["dataset_query"]["dsl_source"]["python_source"] == PYTHON_DSL
    assert set(runtime["dataset_query"]["derivatives"]) == {
        "stock_pool_member",
        "momentum",
        "ret0",
    }
    assert "circ_mv" in runtime["dataset_query"]["factors"]


def test_factor_index_pool_is_injected_only_into_runtime_dataset() -> None:
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "lookback": "P0D",
            "codes": [],
            "factors": [],
            "derivatives": {
                "stock_pool_member": {
                    "type": "DIRECT",
                    "op": "binary.gt",
                    "fields": {"left": "weight_000300SH", "right": 0},
                    "params": {},
                },
            },
            "filters": ["stock_pool_member"],
        },
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
        },
        "factor_columns": ["momentum"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": "circ_mv",
    })

    stored = request.stored_payload()
    runtime = compile_application_payload("factor", stored)

    assert "stock_pool_member" not in stored["dataset_query"]["derivatives"]
    assert "stock_pool_member" not in stored["dataset_query"]["filters"]
    assert "stock_pool_member" not in stored["dataset_query"]["dsl_source"]["json_source"]
    assert "stock_pool_member" not in stored["dataset_query"]["dsl_source"]["python_source"]
    member = runtime["dataset_query"]["derivatives"]["stock_pool_member"]
    assert member["fields"]["left"] == "weight_000300SH"
    assert runtime["dataset_query"]["filters"][0] == "stock_pool_member"
    assert "momentum" in runtime["dataset_query"]["derivatives"]
    assert "ret0" in runtime["dataset_query"]["derivatives"]


def test_factor_index_pool_allows_additional_first_stage_filters() -> None:
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "lookback": "P5D",
            "codes": [],
            "factors": [],
            "derivatives": {
                "stock_pool_member": {
                    "type": "DIRECT",
                    "op": "binary.gt",
                    "fields": {
                        "left": "weight_000300SH",
                        "right": 0,
                    },
                    "params": {},
                },
                "liquid": {
                    "type": "DIRECT",
                    "op": "binary.gt",
                    "fields": {"left": "amount", "right": 1_000_000},
                    "params": {},
                },
            },
            "filters": ["stock_pool_member", "liquid"],
        },
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
        },
        "factor_columns": ["momentum"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": "circ_mv",
    })

    runtime = request.runtime_payload()

    assert runtime["codes_query"]["filters"] == [
        "stock_pool_member",
        "liquid",
    ]
    assert runtime["dataset_query"]["filters"][0] == "stock_pool_member"
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"][
        "fields"
    ]["left"] == "weight_000300SH"


def test_factor_dsl_may_reference_the_runtime_stock_pool_node() -> None:
    json_source = json.dumps({
        "factors": [],
        "derivatives": {
            "pool_rank": {
                "type": "CS",
                "op": "unary.rank_pct",
                "fields": {"col": "turnover_rate_f"},
                "params": {
                    "ascending": True,
                    "ties_method": "average",
                },
                "on": "stock_pool_member",
            },
        },
        "filters": [],
    })
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "lookback": "P0D",
            "codes": [],
            "factors": [],
            "derivatives": {
                "stock_pool_member": {
                    "type": "DIRECT",
                    "op": "binary.gt",
                    "fields": {"left": "weight_000300SH", "right": 0},
                    "params": {},
                },
            },
            "filters": ["stock_pool_member"],
        },
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
            "dsl_source": {
                "language": "json",
                "json_source": json_source,
                "python_source": "inactive Python draft",
            },
        },
        "factor_columns": ["pool_rank"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
        "preprocess": True,
        "market_value_column": "circ_mv",
    })

    stored = request.stored_payload()
    runtime = request.runtime_payload()
    stored_document = json.loads(
        stored["dataset_query"]["dsl_source"]["json_source"]
    )

    assert "stock_pool_member" not in stored_document["derivatives"]
    assert stored_document["derivatives"]["pool_rank"]["on"] == (
        "stock_pool_member"
    )
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"][
        "fields"
    ]["left"] == "weight_000300SH"
    assert runtime["dataset_query"]["derivatives"]["pool_rank"]["on"] == (
        "stock_pool_member"
    )

    raw_parameters = json.loads(json.dumps(stored))
    raw_parameters["dataset_query"].pop("dsl_source")
    raw_runtime = FactorAnalysisApplicationRequest.model_validate(
        raw_parameters
    ).runtime_payload()
    assert raw_runtime["dataset_query"]["derivatives"]["pool_rank"]["on"] == (
        "stock_pool_member"
    )


def test_factor_full_market_injects_true_stock_pool_without_filtering() -> None:
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": None,
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
        },
        "factor_columns": ["momentum"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
    })

    runtime = request.runtime_payload()

    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"] == {
        "type": "DIRECT",
        "op": "nullary.true",
        "fields": {},
        "params": {},
    }
    assert "stock_pool_member" not in runtime["dataset_query"]["filters"]


def test_factor_full_market_dsl_may_reference_true_stock_pool() -> None:
    json_source = json.dumps({
        "factors": [],
        "derivatives": {
            "pool_rank": {
                "type": "CS",
                "op": "unary.rank_pct",
                "fields": {"col": "turnover_rate_f"},
                "params": {
                    "ascending": True,
                    "ties_method": "average",
                },
                "on": "stock_pool_member",
            },
        },
        "filters": [],
    })
    request = FactorAnalysisApplicationRequest.model_validate({
        "codes_query": None,
        "dataset_query": {
            **query_request(),
            "codes": [],
            "derivatives": {
                "ret0": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close"},
                    "params": {"periods": 1},
                },
            },
            "dsl_source": {
                "language": "json",
                "json_source": json_source,
                "python_source": "inactive Python draft",
            },
        },
        "factor_columns": ["pool_rank"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
    })

    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert "stock_pool_member" not in stored["dataset_query"]["derivatives"]
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"][
        "op"
    ] == "nullary.true"
    assert runtime["dataset_query"]["derivatives"]["pool_rank"]["on"] == (
        "stock_pool_member"
    )
    assert "stock_pool_member" not in runtime["dataset_query"]["filters"]


def test_factor_saved_sources_remove_legacy_managed_pool_nodes() -> None:
    json_source = """{
  "factors": ["close"],
  "derivatives": {
    "stock_pool_member": {
      "type": "DIRECT",
      "op": "binary.gt",
      "fields": {"left": "weight_000300SH", "right": 0},
      "params": {}
    }
  },
  "filters": ["stock_pool_member"]
}"""
    python_source = """
member = DIRECT.binary_gt(
    "stock_pool_member",
    left="weight_000300SH",
    right=0,
)
FACTORS = ["close"]
DERIVATIVES = [member]
FILTERS = [member]
""".strip()

    sanitized = factor_dsl_source(
        DslSource.model_validate({
            "language": "python",
            "json_source": json_source,
            "python_source": python_source,
        }),
        [],
    )

    assert sanitized.language == "python"
    assert "stock_pool_member" not in sanitized.json_source
    assert "stock_pool_member" not in sanitized.python_source
    assert compile_dsl_source(sanitized) == {
        "factors": ["close"],
        "derivatives": {},
        "filters": [],
    }


def test_backtest_python_dataset_is_compiled_before_submission() -> None:
    request = BacktestApplicationRequest.model_validate({
        "codes_query": None,
        "dataset_query": query_request(),
        "callbacks": CALLBACKS,
    })
    stored = request.stored_payload()
    runtime = compile_application_payload("backtest", stored)

    assert stored["dataset_query"]["dsl_source"]["language"] == "python"
    assert runtime["dataset_query"]["factors"] == ["close"]
    assert set(runtime["dataset_query"]["derivatives"]) == {"momentum"}
    assert "dsl_source" not in runtime["dataset_query"]


def test_legacy_runtime_payload_is_not_rewritten() -> None:
    payload = {"dataset_query": {"value": "legacy"}}
    assert compile_application_payload("query", payload) is payload


def test_workspace_writes_compiled_input_without_replacing_saved_source(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    stored = QueryApplicationRequest.model_validate(
        query_request()
    ).stored_payload()
    workspace = WorkflowWorkspace(
        id=1,
        user_id=1,
        application="query",
        workspace_key="a" * 32,
    )
    attempt = WorkflowAttempt(
        id=1,
        workflow_workspace_id=1,
        input_json=stored,
        requested_outputs=["data"],
    )

    prepare_workspace(workspace, attempt, create_directory=True)

    written = json.loads(
        workspace_input_file("query", workspace.workspace_key).read_text(
            encoding="utf-8"
        )
    )
    assert written["dataset_query"]["factors"] == ["close"]
    assert "dsl_source" not in written["dataset_query"]
    assert attempt.input_json["dataset_query"]["dsl_source"]["python_source"] == PYTHON_DSL


def test_language_selects_between_two_independent_sources() -> None:
    python_request = query_request()
    json_request = {
        **python_request,
        "dsl_source": {
            **python_request["dsl_source"],
            "language": "json",
        },
    }

    python_runtime = QueryApplicationRequest.model_validate(
        python_request
    ).runtime_payload()
    json_runtime = QueryApplicationRequest.model_validate(
        json_request
    ).runtime_payload()

    assert python_runtime["dataset_query"]["factors"] == ["close"]
    assert json_runtime["dataset_query"]["factors"] == ["vol"]


def test_missing_source_generates_both_editable_versions() -> None:
    request = query_request()
    request.pop("dsl_source")
    stored = QueryApplicationRequest.model_validate(request).stored_payload()
    source = stored["dataset_query"]["dsl_source"]

    assert source["language"] == "json"
    assert json.loads(source["json_source"])["factors"] == ["vol"]
    compiled_python = QueryApplicationRequest.model_validate({
        **stored["dataset_query"],
        "dsl_source": {**source, "language": "python"},
    }).runtime_payload()
    assert compiled_python["dataset_query"]["factors"] == ["vol"]


def test_legacy_single_source_is_upgraded_without_changing_active_text() -> None:
    request = query_request()
    request["dsl_source"] = {
        "language": "python",
        "source": PYTHON_DSL,
    }
    stored = QueryApplicationRequest.model_validate(request).stored_payload()
    source = stored["dataset_query"]["dsl_source"]

    assert source["language"] == "python"
    assert source["python_source"] == PYTHON_DSL
    assert json.loads(source["json_source"])["factors"] == ["close"]


def test_legacy_sources_are_upgraded_in_nested_response_payloads() -> None:
    payload = {
        "codes_query": None,
        "dataset_query": {
            "dsl_source": {
                "language": "python",
                "source": PYTHON_DSL,
            },
        },
    }

    upgraded = upgrade_dsl_sources(payload)
    source = upgraded["dataset_query"]["dsl_source"]
    assert source["language"] == "python"
    assert source["python_source"] == PYTHON_DSL
    assert json.loads(source["json_source"])["factors"] == ["close"]


def test_source_upgrade_does_not_reinterpret_backtest_business_parameters() -> None:
    payload = {
        "params": {"dsl_source": "strategy-value"},
        "config": {
            "nested": {
                "dsl_source": {"language": "custom", "source": "unchanged"},
            },
        },
        "dataset_query": {
            "dsl_source": {
                "language": "python",
                "source": PYTHON_DSL,
            },
        },
    }

    upgraded = upgrade_dsl_sources(payload)

    assert upgraded["params"] == payload["params"]
    assert upgraded["config"] == payload["config"]
    assert upgraded["dataset_query"]["dsl_source"]["python_source"] == PYTHON_DSL
