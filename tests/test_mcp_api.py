"""MCP catalog and HTTP endpoint coverage."""

import asyncio
import json
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import MCPSettings
from core.apps.backtest.views import router as backtest_router
from core.apps.factor.views import router as factor_router
from core.apps.mcp.services import MCP_DOCUMENT_INDEX, mcp_document
from core.apps.mcp.views import router
from core.apps.query.views import router as query_router
from core.apps.users.services import get_current_user
from core.mcp.schemas import DocumentName
from core.mcp.server import mcp_server
from core.mcp.views.projects import validate_project_sort_field


@pytest.fixture
def client() -> TestClient:
    application = FastAPI()
    application.include_router(router)
    application.include_router(query_router)
    application.include_router(factor_router)
    application.include_router(backtest_router)
    application.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(application)


def test_mcp_catalog_lists_each_registered_document_once(client: TestClient) -> None:
    response = client.get("/api/v1/mcp")

    assert response.status_code == 200
    payload = response.json()
    slugs = [item["slug"] for section in payload["sections"] for item in section["items"]]
    assert payload["mcp_url"] == MCPSettings.ENDPOINT_URL
    assert payload["total"] == len(MCP_DOCUMENT_INDEX)
    assert len(slugs) == len(set(slugs)) == payload["total"]
    assert set(slugs) == set(get_args(DocumentName.__value__))
    assert all(mcp_document(slug).content.startswith("# ") for slug in slugs)
    assert "content" not in payload["sections"][0]["items"][0]


def test_mcp_detail_returns_rendered_markdown(client: TestClient) -> None:
    response = client.get("/api/v1/mcp/overview/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["slug"] == "overview/overview"
    assert payload["section"] == "overview"
    assert payload["content"].startswith("# Arena MCP 总览")
    assert "{ARENA_PUBLIC_URL}" not in payload["content"]
    assert "{ARENA_WEB_URL}" not in payload["content"]


def test_mcp_detail_rejects_unregistered_paths(client: TestClient) -> None:
    response = client.get("/api/v1/mcp/not/registered")

    assert response.status_code == 404
    with pytest.raises(FileNotFoundError):
        mcp_document("../main")


def test_mcp_api_requires_authentication() -> None:
    application = FastAPI()
    application.include_router(router)

    response = TestClient(application).get("/api/v1/mcp")

    assert response.status_code == 401


def test_list_projects_tool_exposes_search_and_sort_contract() -> None:
    tools = asyncio.run(mcp_server.list_tools())
    tool = next(item for item in tools if item.name == "list_projects")
    properties = tool.input_schema["properties"]

    assert properties["search"]["default"] is None
    assert properties["sort_by"]["default"] == "updated_at"
    assert properties["sort_order"]["default"] == "desc"
    assert validate_project_sort_field("query", "state") == "state"
    with pytest.raises(ValueError, match="query 项目不支持按 sharpeRatio 排序"):
        validate_project_sort_field("query", "sharpeRatio")


def test_compile_python_dsl_tool_returns_json_or_actionable_error() -> None:
    success = asyncio.run(
        mcp_server.call_tool(
            "compile_python_dsl",
            {
                "python_source": (
                    'valid_close = DIRECT.binary.gt("valid_close", left="close", right=0)\n'
                    'FACTORS = ["close"]\n'
                    "FILTERS = [valid_close]"
                ),
                "application": "query",
            },
        )
    ).structured_content["result"]

    assert success == {
        "application": "query",
        "success": True,
        "compiled_json": {
            "factors": ["close"],
            "derivatives": {
                "valid_close": {
                    "type": "DIRECT",
                    "op": "binary.gt",
                    "fields": {"left": "close", "right": 0},
                    "params": {},
                }
            },
            "filters": ["valid_close"],
        },
        "error_reason": None,
    }

    failure = asyncio.run(
        mcp_server.call_tool(
            "compile_python_dsl",
            {
                "python_source": 'FACTORS = ["not_a_real_field"]\nFILTERS = []',
                "application": "query",
            },
        )
    ).structured_content["result"]

    assert failure["application"] == "query"
    assert failure["success"] is False
    assert failure["compiled_json"] is None
    assert "not_a_real_field" in failure["error_reason"]


@pytest.mark.parametrize("application", ["factor", "backtest"])
@pytest.mark.parametrize("usage", ["definition", "output", "filter"])
def test_dataset_compilers_reject_managed_stock_pool_usage(
    client: TestClient,
    application: str,
    usage: str,
) -> None:
    document = {"factors": ["close"], "derivatives": {}, "filters": []}
    declaration = ""
    if usage != "output":
        document["derivatives"]["stock_pool_member"] = {
            "type": "DIRECT", "op": "nullary.true", "fields": {}, "params": {},
        }
        declaration = 'member = DIRECT.nullary.true("stock_pool_member")\n'
    if usage == "output":
        document["factors"].append("stock_pool_member")
    elif usage == "filter":
        document["filters"].append("stock_pool_member")
    python_filters = "[member]" if usage == "filter" else "[]"
    python_source = (
        f'{declaration}FACTORS = {document["factors"]!r}\n'
        f'FILTERS = {python_filters}'
    )

    result = asyncio.run(mcp_server.call_tool("compile_python_dsl", {
        "application": application,
        "python_source": python_source,
    })).structured_content["result"]

    assert result["success"] is False
    assert result["compiled_json"] is None
    assert "stock_pool_member" in result["error_reason"]
    for language in ("python", "json"):
        response = client.post(f"/api/v1/{application}/dsl/compile", json={
            "language": language,
            "python_source": python_source,
            "json_source": json.dumps(document),
        })
        assert response.status_code == 422
        assert "stock_pool_member" in response.json()["detail"]


@pytest.mark.parametrize("name", ["circ_mv", "industry_l0", "weight_000300SH", "ret0"])
def test_factor_compilers_reject_other_reserved_outputs(client: TestClient, name: str) -> None:
    python_source = (
        f'signal = DIRECT.binary.add("{name}", left="close", right=0)\n'
        'FACTORS = []\nFILTERS = []'
    )
    result = asyncio.run(mcp_server.call_tool("compile_python_dsl", {
        "application": "factor",
        "python_source": python_source,
    })).structured_content["result"]

    assert result["success"] is False
    assert name in result["error_reason"]
    response = client.post("/api/v1/factor/dsl/compile", json={
        "language": "python", "python_source": python_source, "json_source": "inactive",
    })
    assert response.status_code == 422
    assert name in response.json()["detail"]


@pytest.mark.parametrize("application", ["factor", "backtest"])
def test_dataset_compilers_allow_managed_stock_pool_references(client: TestClient, application: str) -> None:
    python_source = (
        'rank = CS.unary.rank_pct("pool_rank", col="close", on="stock_pool_member")\n'
        'FACTORS = []\nFILTERS = []'
    )
    result = asyncio.run(mcp_server.call_tool("compile_python_dsl", {
        "application": application,
        "python_source": python_source,
    })).structured_content["result"]

    assert result["success"] is True
    assert result["compiled_json"]["derivatives"]["pool_rank"]["on"] == "stock_pool_member"
    assert "stock_pool_member" not in result["compiled_json"]["derivatives"]
    response = client.post(f"/api/v1/{application}/dsl/compile", json={
        "language": "python", "python_source": python_source, "json_source": "inactive",
    })
    assert response.status_code == 200
    assert response.json() == result["compiled_json"]


@pytest.mark.parametrize("application", ["factor", "backtest"])
def test_json_dataset_cannot_filter_external_membership_directly(client: TestClient, application: str) -> None:
    response = client.post(f"/api/v1/{application}/dsl/compile", json={
        "language": "json",
        "python_source": "inactive",
        "json_source": json.dumps({
            "factors": ["close"], "derivatives": {}, "filters": ["stock_pool_member"],
        }),
    })

    assert response.status_code == 422
    assert "stock_pool_member" in response.json()["detail"]


def test_codes_query_compiler_still_allows_authored_membership(client: TestClient) -> None:
    python_source = (
        'member = DIRECT.binary.gt("stock_pool_member", left="weight_000300SH", right=0)\n'
        'FACTORS = []\nFILTERS = [member]'
    )
    result = asyncio.run(mcp_server.call_tool("compile_python_dsl", {
        "application": "query",
        "python_source": python_source,
    })).structured_content["result"]

    assert result["success"] is True
    assert result["compiled_json"]["filters"] == ["stock_pool_member"]
    response = client.post("/api/v1/query/dsl/compile", json={
        "language": "python", "python_source": python_source, "json_source": "inactive",
    })
    assert response.status_code == 200
    assert response.json() == result["compiled_json"]
