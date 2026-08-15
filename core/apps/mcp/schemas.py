"""Schemas returned by the Arena MCP API."""

from pydantic import BaseModel, ConfigDict


class McpDocumentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    description: str


class McpSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    description: str
    items: tuple[McpDocumentSummary, ...]


class McpCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    mcp_url: str
    sections: tuple[McpSection, ...]
    total: int


class McpDocument(McpDocumentSummary):
    section: str
    content: str


__all__ = ["McpCatalog", "McpDocument", "McpDocumentSummary", "McpSection"]
