"""HTTP request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    created_at: datetime
    updated_at: datetime


class McpConfiguration(BaseModel):
    """User-controlled instructions and destructive MCP permissions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    custom_prompt: str = Field(max_length=16000)
    allow_delete_query_projects: bool
    allow_delete_factor_projects: bool
    allow_delete_backtest_projects: bool
    allow_delete_factor_versions: bool
    allow_delete_backtest_versions: bool
    allow_delete_fee_analyses: bool
    allow_delete_sensitivity_analyses: bool
    allow_delete_optimizations: bool

    @field_validator("custom_prompt", mode="before")
    @classmethod
    def normalize_custom_prompt(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AuthenticationResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
