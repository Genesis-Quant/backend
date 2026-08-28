"""Feedback API request and response schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

type FeedbackSource = Literal["web", "mcp"]
FeedbackContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
    Field(
        description="对 Arena 功能、数据、文档或使用体验的反馈正文。",
    ),
]


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    content: FeedbackContent


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    source: FeedbackSource
    created_at: datetime
