"""HTTP request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
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


class AuthenticationResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
