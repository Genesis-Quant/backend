import json

import pytest
from pydantic import ValidationError

from config import ArenaSettings
from core.apps.workflows.artifacts import workspace_input_file
from core.apps.workflows.models import WorkflowAttempt, WorkflowWorkspace
from core.apps.workflows.services import prepare_workspace
from core.utils.dsl_source import (
    BacktestApplicationRequest,
    DslSource,
    FactorAnalysisApplicationRequest,
    QueryApplicationRequest,
    compile_application_payload,
    compile_dsl_source,
    compile_factor_dsl_source,
)


PYTHON_DSL = """
momentum = TS.unary.pct_change(
    "momentum",
    col="close",
    periods=20,
)

FACTORS = ["close"]
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


def query_request(*, codes: list[str] | None = None) -> dict:
    return {
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "lookback": "P30D",
        "codes": ["000001.SZ"] if codes is None else codes,
        "factors": ["stale_preview"],
        "derivatives": {},
        "filters": [],
        "dsl_source": {
            "language": "python",
            "json_source": """{
  "factors": ["open"],
  "derivatives": {},
  "filters": []
}""",
            "python_source": PYTHON_DSL,
        },
    }


def factor_request(*, codes_query: dict | None = None) -> dict:
    dataset = query_request(codes=[])
    dataset["derivatives"] = {
        "ret0": {
            "type": "TS",
            "op": "unary.pct_change",
            "fields": {"col": "close"},
            "params": {"periods": 1},
        },
    }
    return {
        "codes_query": codes_query,
        "dataset_query": dataset,
        "factor_columns": ["momentum"],
        "return_columns": ["ret0"],
        "return_specs": {"ret0": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": "circ_mv",
        "industry_column": "industry",
    }


def index_query(*, right: int = 0) -> dict:
    document = {
        "factors": [],
        "derivatives": {
            "stock_pool_member": {
                "type": "DIRECT",
                "op": "binary.gt",
                "fields": {"left": "weight_000300SH", "right": right},
                "params": {},
            },
        },
        "filters": ["stock_pool_member"],
    }
    return {
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "lookback": "P0D",
        "codes": [],
        **document,
        "dsl_source": {
            "language": "json",
            "json_source": json.dumps(document, ensure_ascii=False),
            "python_source": "inactive source may be invalid",
        },
    }


def backtest_request() -> dict:
    return {
        "config": {
            "cash": 1_000_000,
            "commission": 0.0003,
            "tax": 0.001,
            "enableMinimumPerTransactionFee": True,
        },
        "params": {},
        "codes_query": None,
        "dataset_query": query_request(),
        "adj": None,
        "annual_trading_days": 250,
        "risk_free_rate": 0.04,
        "utils": "",
        "callbacks": CALLBACKS,
    }


def test_query_sources_are_stored_verbatim_and_only_active_source_is_compiled() -> None:
    raw = query_request()
    request = QueryApplicationRequest.model_validate(raw)

    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert stored == {"dataset_query": raw}
    assert runtime["dataset_query"]["factors"] == ["close"]
    assert set(runtime["dataset_query"]["derivatives"]) == {"momentum"}
    assert "dsl_source" not in runtime["dataset_query"]


def test_inactive_source_is_never_parsed() -> None:
    raw = query_request()
    raw["dsl_source"] = {
        "language": "json",
        "json_source": raw["dsl_source"]["json_source"],
        "python_source": "this is deliberately invalid",
    }

    request = QueryApplicationRequest.model_validate(raw)

    assert request.runtime_payload()["dataset_query"]["factors"] == ["open"]
    assert request.stored_payload()["dataset_query"]["dsl_source"]["python_source"] == "this is deliberately invalid"


@pytest.mark.parametrize("missing", ["language", "json_source", "python_source"])
def test_every_source_field_is_required(missing: str) -> None:
    raw = query_request()
    del raw["dsl_source"][missing]

    with pytest.raises(ValidationError):
        QueryApplicationRequest.model_validate(raw)


def test_query_payload_without_source_is_rejected_instead_of_upgraded() -> None:
    raw = query_request()
    del raw["dsl_source"]

    with pytest.raises(ValidationError):
        compile_application_payload("query", {"dataset_query": raw})


def test_factor_compiler_accepts_reference_to_managed_stock_pool() -> None:
    source = DslSource(
        language="python",
        json_source="inactive JSON",
        python_source='''
rank = CS.unary.rank_pct(
    "pool_rank",
    col="turnover_rate_f",
    on="stock_pool_member",
)
FACTORS = []
FILTERS = []
'''.strip(),
    )

    assert compile_factor_dsl_source(source)["derivatives"]["pool_rank"]["on"] == "stock_pool_member"


def test_full_market_factor_injection_changes_only_runtime_copy() -> None:
    raw = factor_request()
    request = FactorAnalysisApplicationRequest.model_validate(raw)

    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert stored == raw
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"] == {
        "type": "DIRECT",
        "op": "nullary.true",
        "fields": {},
        "params": {},
    }
    assert "stock_pool_member" not in runtime["dataset_query"]["filters"]
    assert set(runtime["dataset_query"]["derivatives"]) >= {"momentum", "ret0", "stock_pool_member"}
    assert "dsl_source" not in runtime["dataset_query"]


def test_index_pool_is_compiled_and_injected_only_into_runtime_copy() -> None:
    raw = factor_request(codes_query=index_query())
    request = FactorAnalysisApplicationRequest.model_validate(raw)

    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert stored == raw
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"]["fields"]["left"] == "weight_000300SH"
    assert runtime["dataset_query"]["filters"][0] == "stock_pool_member"


def test_malformed_dynamic_pool_is_rejected_instead_of_ignored() -> None:
    with pytest.raises(ValidationError, match="binary.gt"):
        FactorAnalysisApplicationRequest.model_validate(
            factor_request(codes_query=index_query(right=1))
        )


def test_user_cannot_define_backend_managed_factor_nodes() -> None:
    raw = factor_request()
    document = {
        "factors": [],
        "derivatives": {
            "momentum": {
                "type": "TS",
                "op": "unary.pct_change",
                "fields": {"col": "close"},
                "params": {"periods": 20},
            },
            "stock_pool_member": {
                "type": "DIRECT",
                "op": "nullary.true",
                "fields": {},
                "params": {},
            },
        },
        "filters": [],
    }
    raw["dataset_query"]["dsl_source"] = {
        "language": "json",
        "json_source": json.dumps(document),
        "python_source": "inactive",
    }

    with pytest.raises(ValidationError, match="保留列"):
        FactorAnalysisApplicationRequest.model_validate(raw)


def test_factor_requires_explicit_return_specs() -> None:
    raw = factor_request()
    del raw["return_specs"]

    with pytest.raises(ValidationError):
        FactorAnalysisApplicationRequest.model_validate(raw)


def test_factor_requires_every_return_spec_field() -> None:
    raw = factor_request()
    raw["return_specs"]["ret0"] = {}

    with pytest.raises(ValidationError):
        FactorAnalysisApplicationRequest.model_validate(raw)


def test_backtest_compiles_dataset_without_rewriting_stored_sources() -> None:
    raw = backtest_request()
    request = BacktestApplicationRequest.model_validate(raw)

    stored = request.stored_payload()
    runtime = request.runtime_payload()

    assert stored == raw
    assert runtime["dataset_query"]["factors"] == ["close"]
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"]["op"] == "nullary.true"
    assert "dsl_source" not in runtime["dataset_query"]


@pytest.mark.parametrize("language", ["json", "python"])
def test_backtest_accepts_custom_dynamic_pool_without_factor_index_restriction(language: str) -> None:
    raw = backtest_request()
    pool = index_query()
    document = json.loads(pool["dsl_source"]["json_source"])
    document["derivatives"]["stock_pool_member"]["fields"]["left"] = "vol"
    pool["dsl_source"] = {
        "language": language,
        "json_source": json.dumps(document),
        "python_source": (
            'member = DIRECT.binary.gt("stock_pool_member", left="vol", right=0)\n'
            'FACTORS = []\nFILTERS = [member]\n'
        ),
    }
    raw["codes_query"] = pool

    query = QueryApplicationRequest.model_validate(pool).runtime_payload()["dataset_query"]
    request = BacktestApplicationRequest.model_validate(raw)
    runtime = request.runtime_payload()

    assert request.stored_payload() == raw
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"] == query["derivatives"]["stock_pool_member"]
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"]["fields"]["left"] == "vol"
    assert runtime["dataset_query"]["filters"] == []
    with pytest.raises(ValidationError, match="受支持指数权重"):
        FactorAnalysisApplicationRequest.model_validate(factor_request(codes_query=pool))


def custom_pool_with_dependencies() -> dict:
    pool = index_query()
    document = {
        "factors": [],
        "derivatives": {
            "eligible": {
                "type": "DIRECT", "op": "binary.gt",
                "fields": {"left": "vol", "right": 0}, "params": {},
            },
            "liquidity_mean": {
                "type": "TS", "op": "unary.rolling_mean",
                "fields": {"col": "vol"}, "params": {"window": 5}, "on": "eligible",
            },
            "stock_pool_member": {
                "type": "DIRECT", "op": "binary.gt",
                "fields": {
                    "left": {
                        "type": "DIRECT", "op": "binary.mul",
                        "fields": {"left": "liquidity_mean", "right": 2}, "params": {},
                    },
                    "right": 1000,
                },
                "params": {},
            },
            "unused": {
                "type": "DIRECT", "op": "nullary.true", "fields": {}, "params": {},
            },
        },
        "filters": ["stock_pool_member", "unused"],
    }
    pool["dsl_source"]["json_source"] = json.dumps(document)
    return pool


def test_backtest_injects_recursive_pool_dependencies_without_copying_unrelated_columns() -> None:
    raw = backtest_request()
    raw["codes_query"] = custom_pool_with_dependencies()

    request = BacktestApplicationRequest.model_validate(raw)
    runtime = request.runtime_payload()
    derivatives = runtime["dataset_query"]["derivatives"]

    assert set(derivatives) == {"momentum", "eligible", "liquidity_mean", "stock_pool_member"}
    for name in ("eligible", "liquidity_mean", "stock_pool_member"):
        assert derivatives[name] == runtime["codes_query"]["derivatives"][name]
    assert runtime["dataset_query"]["filters"] == []
    assert request.stored_payload() == raw


def test_backtest_reuses_identical_pool_dependencies_after_node_validation() -> None:
    raw = backtest_request()
    raw["codes_query"] = custom_pool_with_dependencies()
    document = json.loads(raw["codes_query"]["dsl_source"]["json_source"])
    # The default min_periods is omitted in one source and explicit in the other.
    document["derivatives"]["liquidity_mean"]["params"]["min_periods"] = None
    del document["derivatives"]["stock_pool_member"]
    del document["derivatives"]["unused"]
    document["filters"] = ["eligible"]
    raw["dataset_query"]["dsl_source"]["language"] = "json"
    raw["dataset_query"]["dsl_source"]["json_source"] = json.dumps(document)

    request = BacktestApplicationRequest.model_validate(raw)
    runtime = request.runtime_payload()
    derivatives = runtime["dataset_query"]["derivatives"]

    assert set(derivatives) == {"eligible", "liquidity_mean", "stock_pool_member"}
    for name in derivatives:
        assert derivatives[name] == runtime["codes_query"]["derivatives"][name]
    assert runtime["dataset_query"]["filters"] == ["eligible"]
    assert request.stored_payload() == raw


@pytest.mark.parametrize("collision", ["eligible", "vol"])
def test_backtest_rejects_changed_pool_dependencies_and_shadowed_base_fields(collision: str) -> None:
    raw = backtest_request()
    raw["codes_query"] = custom_pool_with_dependencies()
    document = {
        "factors": [],
        "derivatives": {
            collision: {
                "type": "DIRECT", "op": "nullary.true", "fields": {}, "params": {},
            },
        },
        "filters": [],
    }
    raw["dataset_query"]["dsl_source"]["language"] = "json"
    raw["dataset_query"]["dsl_source"]["json_source"] = json.dumps(document)

    with pytest.raises(ValidationError, match="股票池依赖定义冲突"):
        BacktestApplicationRequest.model_validate(raw)


def test_backtest_requires_dynamic_membership_to_participate_in_first_stage_filters() -> None:
    raw = backtest_request()
    pool = index_query()
    document = json.loads(pool["dsl_source"]["json_source"])
    document["filters"] = []
    pool["dsl_source"]["json_source"] = json.dumps(document)
    raw["codes_query"] = pool

    with pytest.raises(ValidationError, match="filters 必须包含 stock_pool_member"):
        BacktestApplicationRequest.model_validate(raw)


def test_backtest_rejects_non_bool_dynamic_membership() -> None:
    raw = backtest_request()
    pool = index_query()
    document = json.loads(pool["dsl_source"]["json_source"])
    document["derivatives"]["stock_pool_member"]["op"] = "binary.add"
    pool["dsl_source"]["json_source"] = json.dumps(document)
    raw["codes_query"] = pool

    with pytest.raises(ValidationError, match="必须返回 BOOL"):
        BacktestApplicationRequest.model_validate(raw)


def test_backtest_dataset_can_reference_managed_stock_pool() -> None:
    raw = backtest_request()
    document = {
        "factors": [],
        "derivatives": {
            "pool_rank": {
                "type": "CS",
                "op": "unary.rank_pct",
                "fields": {"col": "turnover_rate_f"},
                "params": {"ascending": True, "ties_method": "average"},
                "on": "stock_pool_member",
            },
        },
        "filters": [],
    }
    raw["dataset_query"]["dsl_source"] = {
        "language": "json",
        "json_source": json.dumps(document),
        "python_source": "inactive",
    }

    runtime = BacktestApplicationRequest.model_validate(raw).runtime_payload()

    assert runtime["dataset_query"]["derivatives"]["pool_rank"]["on"] == "stock_pool_member"
    assert runtime["dataset_query"]["derivatives"]["stock_pool_member"]["op"] == "nullary.true"


def test_backtest_dataset_cannot_define_managed_stock_pool() -> None:
    raw = backtest_request()
    document = {
        "factors": [],
        "derivatives": {
            "stock_pool_member": {
                "type": "DIRECT",
                "op": "nullary.true",
                "fields": {},
                "params": {},
            },
        },
        "filters": [],
    }
    raw["dataset_query"]["dsl_source"] = {
        "language": "json",
        "json_source": json.dumps(document),
        "python_source": "inactive",
    }

    with pytest.raises(ValidationError, match="不能定义或过滤"):
        BacktestApplicationRequest.model_validate(raw)


def test_backtest_requires_all_callbacks() -> None:
    raw = backtest_request()
    del raw["callbacks"]["onTrade"]

    with pytest.raises(ValidationError):
        BacktestApplicationRequest.model_validate(raw)


def test_backtest_requires_explicit_base_config() -> None:
    raw = backtest_request()
    del raw["config"]["cash"]

    with pytest.raises(ValidationError, match="config 缺少必填基础配置"):
        BacktestApplicationRequest.model_validate(raw)


def test_workspace_writes_runtime_copy_without_mutating_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    raw = query_request()
    stored = QueryApplicationRequest.model_validate(raw).stored_payload()
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
    assert attempt.input_json == stored


def test_compile_application_payload_rejects_unknown_or_unwrapped_input() -> None:
    with pytest.raises(ValueError, match="必须且只能包含"):
        compile_application_payload("query", query_request())
    with pytest.raises(ValueError, match="不支持"):
        compile_application_payload("unknown", {})


def test_json_dsl_requires_complete_document() -> None:
    source = DslSource(
        language="json",
        json_source='{"factors": []}',
        python_source="inactive",
    )

    with pytest.raises(ValueError):
        compile_dsl_source(source)
