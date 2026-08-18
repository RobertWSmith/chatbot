import logging
from uuid import uuid4

from app.services.tool_logging import ToolCallLoggingCallback


def test_tool_callback_logs_start_and_completion_without_argument_values(caplog):
    """Ensure default tool audit logs prove execution without exposing values."""
    callback = ToolCallLoggingCallback(
        logger=logging.getLogger("app.services.agent"),
        user_id=7,
        thread_id="thread-1",
    )
    run_id = uuid4()

    with caplog.at_level(logging.INFO, logger="app.services.agent"):
        callback.on_tool_start(
            {"name": "mcp_portal_web_search"},
            '{"query":"private search"}',
            run_id=run_id,
            metadata={"mcp_namespace": "portal"},
            inputs={"query": "private search"},
        )
        callback.on_tool_end("search result", run_id=run_id)

    assert "event=tool.call.start" in caplog.text
    assert "tool=mcp_portal_web_search" in caplog.text
    assert "source=mcp:portal" in caplog.text
    assert "input=keys:query" in caplog.text
    assert "private search" not in caplog.text
    assert "event=tool.call.end" in caplog.text
    assert "output_chars=13" in caplog.text


def test_tool_callback_sanitizes_opt_in_argument_logging(caplog):
    """Ensure opt-in argument logging redacts credentials recursively."""
    callback = ToolCallLoggingCallback(
        logger=logging.getLogger("app.services.agent"),
        user_id=7,
        thread_id="thread-1",
        log_arguments=True,
    )

    with caplog.at_level(logging.INFO, logger="app.services.agent"):
        callback.on_tool_start(
            {"name": "mcp_portal_public_search"},
            "",
            run_id=uuid4(),
            metadata={"mcp_namespace": "portal"},
            inputs={
                "query": "current weather",
                "headers": {"Authorization": "Bearer top-secret"},
            },
        )

    assert "current weather" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "Bearer top-secret" not in caplog.text


def test_tool_callback_logs_error_type_without_error_message(caplog):
    """Ensure failed tools expose the error class but not exception details."""
    callback = ToolCallLoggingCallback(
        logger=logging.getLogger("app.services.agent"),
        user_id=7,
        thread_id="thread-1",
    )
    run_id = uuid4()

    with caplog.at_level(logging.INFO, logger="app.services.agent"):
        callback.on_tool_start({"name": "recall_user_memory"}, "query", run_id=run_id)
        callback.on_tool_error(RuntimeError("sensitive detail"), run_id=run_id)

    assert "event=tool.call.error" in caplog.text
    assert "source=memory" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "sensitive detail" not in caplog.text
