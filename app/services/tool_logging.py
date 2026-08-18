from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

SENSITIVE_ARGUMENT_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
MAX_LOG_VALUE_LENGTH = 500


class ToolCallLoggingCallback(BaseCallbackHandler):
    """Write privacy-conscious audit logs for LangChain tool executions."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        user_id: int | None,
        thread_id: str,
        log_arguments: bool = False,
    ) -> None:
        """Initialize request-scoped tool-call logging.

        Args:
            logger: Application logger that receives audit events.
            user_id: Authenticated user identifier, when available.
            thread_id: Chat thread associated with the agent run.
            log_arguments: Whether to log sanitized tool argument values.
        """
        self._logger = logger
        self._user_id = user_id
        self._thread_id = thread_id
        self._log_arguments = log_arguments
        self._started_at: dict[UUID, float] = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Log the beginning of an actual tool invocation.

        Args:
            serialized: LangChain serialization containing the tool name.
            input_str: String representation of the tool input.
            run_id: Unique identifier for this tool invocation.
            parent_run_id: Identifier for the parent agent run, when present.
            tags: LangChain tags attached to the invocation.
            metadata: Tool and run metadata, including MCP provenance.
            inputs: Structured tool arguments, when available.
            **kwargs: Additional callback values supplied by LangChain.
        """
        del tags, kwargs
        self._started_at[run_id] = time.perf_counter()
        tool_name = str(serialized.get("name") or "unknown")
        namespace = (metadata or {}).get("mcp_namespace")
        source = f"mcp:{namespace}" if namespace else _tool_source(tool_name)
        input_summary = _tool_input_summary(
            inputs,
            input_str,
            include_values=self._log_arguments,
        )
        self._logger.info(
            "event=tool.call.start user_id=%s thread_id=%s run_id=%s "
            "parent_run_id=%s tool=%s source=%s input=%s",
            self._user_id,
            self._thread_id,
            run_id,
            parent_run_id,
            tool_name,
            source,
            input_summary,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Log successful tool completion without recording result content.

        Args:
            output: Tool output whose type and approximate size are recorded.
            run_id: Unique identifier for this tool invocation.
            parent_run_id: Identifier for the parent agent run, when present.
            **kwargs: Additional callback values supplied by LangChain.
        """
        del kwargs
        self._logger.info(
            "event=tool.call.end user_id=%s thread_id=%s run_id=%s "
            "parent_run_id=%s duration_ms=%s output_type=%s output_chars=%s",
            self._user_id,
            self._thread_id,
            run_id,
            parent_run_id,
            self._duration_ms(run_id),
            type(output).__name__,
            len(str(output)),
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Log failed tool completion without exposing exception details.

        Args:
            error: Exception raised by the tool.
            run_id: Unique identifier for this tool invocation.
            parent_run_id: Identifier for the parent agent run, when present.
            **kwargs: Additional callback values supplied by LangChain.
        """
        del kwargs
        self._logger.error(
            "event=tool.call.error user_id=%s thread_id=%s run_id=%s "
            "parent_run_id=%s duration_ms=%s error_type=%s",
            self._user_id,
            self._thread_id,
            run_id,
            parent_run_id,
            self._duration_ms(run_id),
            type(error).__name__,
        )
        self._logger.debug("Tool call exception", exc_info=error)

    def _duration_ms(self, run_id: UUID) -> int | None:
        """Return elapsed milliseconds and discard stored run timing.

        Args:
            run_id: Unique identifier for a completed tool invocation.

        Returns:
            Elapsed whole milliseconds, or ``None`` if start was not observed.
        """
        started_at = self._started_at.pop(run_id, None)
        if started_at is None:
            return None
        return round((time.perf_counter() - started_at) * 1000)


def _tool_source(tool_name: str) -> str:
    """Infer whether a tool handles local memory or is provided by MCP.

    Args:
        tool_name: Registered LangChain tool name.

    Returns:
        A concise tool-source label.
    """
    return "mcp" if tool_name.startswith("mcp_") else "memory"


def _tool_input_summary(
    inputs: dict[str, Any] | None,
    input_str: str,
    *,
    include_values: bool,
) -> str:
    """Create a bounded, optionally value-free tool-input summary.

    Args:
        inputs: Structured tool arguments, when available.
        input_str: String representation used when structured input is absent.
        include_values: Whether sanitized argument values may be logged.

    Returns:
        A single-line summary safe for structured application logs.
    """
    if not include_values:
        if inputs is not None:
            keys = ",".join(sorted(str(key) for key in inputs)) or "none"
            return f"keys:{keys}"
        return f"values:redacted,length:{len(input_str)}"

    value: Any = _redact_sensitive_values(inputs) if inputs is not None else input_str
    try:
        rendered = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        rendered = str(value)
    rendered = " ".join(rendered.split())
    if len(rendered) > MAX_LOG_VALUE_LENGTH:
        return f"{rendered[:MAX_LOG_VALUE_LENGTH]}..."
    return rendered


def _redact_sensitive_values(value: Any) -> Any:
    """Recursively redact values whose keys look sensitive.

    Args:
        value: Arbitrarily nested tool input value.

    Returns:
        A copy suitable for diagnostic logging.
    """
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in SENSITIVE_ARGUMENT_PARTS)
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_values(item) for item in value]
    return value
