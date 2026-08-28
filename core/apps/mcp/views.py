"""Authenticated MCP documentation endpoints."""

from fastapi import APIRouter, Depends

from core.apps.users.services import get_current_user
from core.utils.http import raise_api_http_error

from .schemas import McpCatalog, McpDocument
from .services import mcp_catalog, mcp_document

router = APIRouter(
    prefix="/api/v1/mcp",
    tags=["mcp"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=McpCatalog)
def catalog() -> McpCatalog:
    return mcp_catalog()


@router.get("/{slug:path}", response_model=McpDocument)
def page(slug: str) -> McpDocument:
    try:
        return mcp_document(slug)
    except FileNotFoundError as error:
        raise_api_http_error(error)


__all__ = ["router"]
