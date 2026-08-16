from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

_SECRET_HEADERS = {"authorization", "cookie", "proxy-authorization"}


class MCPNamespaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    namespace: str = Field(min_length=2, max_length=31, pattern=r"^[a-z][a-z0-9]{1,30}$")
    display_name: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=2_000)
    transport: Literal["http", "streamable_http", "sse"] = "http"
    url: AnyHttpUrl
    auth_token_env_var: str | None = Field(
        default=None,
        max_length=255,
        pattern=r"^[A-Z_][A-Z0-9_]*$",
    )
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("namespace", mode="before")
    @classmethod
    def normalize_namespace(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("auth_token_env_var", mode="before")
    @classmethod
    def empty_auth_token_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("headers")
    @classmethod
    def reject_secret_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(key.lower() in _SECRET_HEADERS for key in value):
            raise ValueError("secret authentication headers must use auth_token_env_var")
        return value

    @model_validator(mode="after")
    def default_display_name(self) -> "MCPNamespaceCreate":
        self.display_name = self.display_name or self.namespace
        return self


class MCPNamespaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    display_name: str
    description: str
    transport: Literal["http", "streamable_http", "sse"]
    url: AnyHttpUrl
    auth_token_env_var: str | None
    headers: dict[str, str]
    enabled: bool
    group_grant_count: int = Field(ge=0)
    created_at: datetime | None


class MCPNamespaceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_namespace: MCPNamespaceResponse


class MCPNamespaceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_namespaces: list[MCPNamespaceResponse]


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
