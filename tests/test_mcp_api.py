"""MCP catalog and HTTP endpoint coverage."""

import asyncio
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import MCPSettings
from core.apps.mcp.services import MCP_DOCUMENT_INDEX, mcp_document
from core.apps.mcp.views import router
from core.apps.users.services import get_current_user
from core.mcp.schemas import DocumentName
from core.mcp.server import mcp_server
from core.mcp.views.projects import validate_project_sort_field


@pytest.fixture
def client() -> TestClient:
    application = FastAPI()
    application.include_router(router)
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
