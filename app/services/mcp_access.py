from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import exists, select

from app.extensions import db
from app.models import GroupMCPNamespace, GroupMembership, MCPNamespace

LOGGER = logging.getLogger(__name__)


class MCPConfigurationError(RuntimeError):
    """Raised when an authorized namespace cannot be configured safely."""


@dataclass(frozen=True)
class MCPToolLoadResult:
    """Describe authorized MCP tools and namespace load outcomes."""

    tools: list[Any]
    loaded_namespaces: tuple[str, ...]
    unavailable_namespaces: tuple[str, ...]


def accessible_mcp_namespaces(user_id: int) -> list[MCPNamespace]:
    """Return enabled MCP namespaces granted through any of a user's groups."""
    return list(db.session.scalars(_accessible_mcp_namespaces_statement(user_id)).all())


def _accessible_mcp_namespaces_statement(user_id: int):
    """Build the query for a user's effective MCP namespaces.

    Args:
        user_id: User whose group grants should be queried.

    Returns:
        A SQLAlchemy select statement for enabled namespaces.
    """
    grant_exists = exists(
        select(GroupMCPNamespace.id)
        .join(
            GroupMembership,
            GroupMembership.group_id == GroupMCPNamespace.group_id,
        )
        .where(
            GroupMCPNamespace.mcp_namespace_id == MCPNamespace.id,
            GroupMembership.user_id == user_id,
        )
    )
    return (
        select(MCPNamespace)
        .where(MCPNamespace.enabled.is_(True), grant_exists)
        .order_by(MCPNamespace.namespace.asc())
    )


async def load_authorized_mcp_tools(user_id: int) -> MCPToolLoadResult:
    """Load a user's MCP tools while isolating individual namespace failures."""
    namespaces = accessible_mcp_namespaces(user_id)
    if not namespaces:
        return MCPToolLoadResult([], (), ())

    results = await asyncio.gather(
        *(_load_namespace_tools(item) for item in namespaces),
        return_exceptions=True,
    )
    tools: list[Any] = []
    loaded: list[str] = []
    unavailable: list[str] = []
    used_tool_names: set[str] = set()

    for item, result in zip(namespaces, results, strict=True):
        if isinstance(result, BaseException):
            LOGGER.warning(
                "MCP namespace %s could not be loaded: %s",
                item.namespace,
                type(result).__name__,
            )
            unavailable.append(item.namespace)
            continue

        loaded.append(item.namespace)
        for tool in result:
            tool.name = _unique_tool_name(tool.name, used_tool_names)
            used_tool_names.add(tool.name)
            tool.description = (
                f"[MCP namespace: {item.namespace}] "
                f"{tool.description or 'No description provided.'}"
            )
            tool.metadata = {
                **(getattr(tool, "metadata", None) or {}),
                "mcp_namespace": item.namespace,
            }
            tools.append(tool)

    LOGGER.info(
        "Loaded MCP namespaces=%s unavailable=%s tools=%s",
        ",".join(loaded) or "none",
        ",".join(unavailable) or "none",
        ",".join(tool.name for tool in tools) or "none",
    )
    return MCPToolLoadResult(tools, tuple(loaded), tuple(unavailable))


async def probe_mcp_namespace(item: MCPNamespace) -> list[str]:
    """Return the tool names advertised by one namespace."""
    return [tool.name for tool in await _load_namespace_tools(item)]


async def _load_namespace_tools(item: MCPNamespace) -> list[Any]:
    """Load tools from one configured MCP namespace.

    Args:
        item: Namespace configuration to connect to.

    Returns:
        Tools reported by the remote MCP server.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_name = f"mcp_{item.namespace}"
    client = MultiServerMCPClient(
        {server_name: _connection_config(item)},
        tool_name_prefix=True,
    )
    return await client.get_tools(server_name=server_name)


def _connection_config(item: MCPNamespace) -> dict[str, Any]:
    """Build a safe remote connection configuration for an MCP namespace.

    Args:
        item: Persisted namespace configuration.

    Returns:
        Configuration accepted by ``MultiServerMCPClient``.

    Raises:
        MCPConfigurationError: If the URL, transport, headers, or referenced
            bearer token is invalid.
    """
    parsed = urlparse(item.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MCPConfigurationError("MCP URL is invalid.")
    if item.transport not in {"http", "streamable_http", "sse"}:
        raise MCPConfigurationError(
            "MCP transport is not supported by the web application."
        )

    raw_headers = item.headers or {}
    if not isinstance(raw_headers, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_headers.items()
    ):
        raise MCPConfigurationError("MCP headers are invalid.")
    headers = dict(raw_headers)
    if item.auth_token_env_var:
        token = os.getenv(item.auth_token_env_var)
        if not token:
            raise MCPConfigurationError("MCP bearer token is not configured.")
        headers["Authorization"] = f"Bearer {token}"

    config: dict[str, Any] = {"transport": item.transport, "url": item.url}
    if headers:
        config["headers"] = headers
    return config


def _unique_tool_name(name: str, used_names: set[str]) -> str:
    """Create a bounded tool name that is unique within a loaded tool set.

    Args:
        name: Tool name reported by an MCP server.
        used_names: Names already assigned during the current load.

    Returns:
        A unique name no longer than 64 characters.
    """
    candidate = _bounded_tool_name(name)
    if candidate not in used_names:
        return candidate
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    base = candidate[:55]
    candidate = f"{base}_{digest}"
    suffix = 2
    while candidate in used_names:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 64 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _bounded_tool_name(name: str) -> str:
    """Bound a tool name to 64 characters while preserving uniqueness data.

    Args:
        name: Original tool name.

    Returns:
        The original name or a truncated name with a stable hash suffix.
    """
    if len(name) <= 64:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:55]}_{digest}"
