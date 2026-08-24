from __future__ import annotations

import json
import logging
from collections.abc import Generator
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    session,
    stream_with_context,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    ChatMessage,
    ChatThread,
    MessageTelemetry,
    ToolCallTelemetry,
    User,
    utcnow,
)
from app.services.agent import stream_agent_response
from app.services.mcp_access import accessible_mcp_namespaces
from app.validation import (
    MODEL_OPTIONS,
    REASONING_OPTIONS,
    REASONING_PROVIDER_OPTIONS,
    validate_settings_update,
)

bp = Blueprint("chat", __name__)
logger = logging.getLogger(__name__)
ACTIVE_THREAD_SESSION_KEY = "active_chat_thread_id"


@bp.get("/chat")
@login_required
def chat_home():
    """Render the active chat, falling back to the most recently updated one."""
    threads = _user_threads()
    if threads:
        active_thread_id = session.get(ACTIVE_THREAD_SESSION_KEY)
        selected = next(
            (thread for thread in threads if thread.id == active_thread_id),
            threads[0],
        )
    else:
        selected = _create_thread()
        threads = [selected]
    _remember_active_thread(selected)
    return _render_chat(selected, threads)


@bp.get("/chat/<thread_id>")
@login_required
def chat_thread(thread_id: str):
    """Render a user-owned chat thread.

    Args:
        thread_id: Chat-thread identifier from the URL.
    """
    selected = _get_thread_or_404(thread_id)
    _remember_active_thread(selected)
    return _render_chat(selected, _user_threads())


@bp.post("/api/chat/threads")
@login_required
def create_thread():
    """Create an empty chat thread for the current user."""
    thread = _create_thread()
    _remember_active_thread(thread)
    return (
        jsonify(
            {
                "thread": {
                    "id": thread.id,
                    "title": thread.title,
                    "model_name": thread.model_name,
                    "reasoning_effort": thread.reasoning_effort,
                    "reasoning_provider": thread.reasoning_provider,
                    "mcp_namespaces": thread.mcp_namespace_filter,
                }
            }
        ),
        201,
    )


@bp.post("/api/chat/threads/<thread_id>/messages")
@login_required
def send_message(thread_id: str):
    """Persist a user message and stream the assistant response.

    Args:
        thread_id: User-owned chat thread receiving the message.
    """
    request_received_at = utcnow()
    thread = _get_thread_or_404(thread_id)
    _remember_active_thread(thread)
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        return jsonify({"error": "Message is required."}), 400

    generation_patch = {
        key: payload[key]
        for key in ("model_name", "reasoning_effort", "reasoning_provider")
        if key in payload
    }
    try:
        namespace_filter = _message_namespace_filter(
            payload,
            thread,
            current_user.id,
        )
        generation_settings = validate_settings_update(
            _thread_generation_settings(thread, current_user.settings.merged()),
            generation_patch,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"errors": exc.args[0]}), 400

    thread.model_name = generation_settings["model_name"]
    thread.reasoning_effort = generation_settings["reasoning_effort"]
    thread.reasoning_provider = generation_settings["reasoning_provider"]
    thread.mcp_namespace_filter = namespace_filter
    thread.updated_at = request_received_at
    generation_settings["mcp_namespaces"] = namespace_filter
    generation_metadata = {
        key: generation_settings[key]
        for key in ("model_name", "reasoning_effort", "reasoning_provider")
    }
    generation_metadata["mcp_namespaces"] = namespace_filter

    user_id = current_user.id
    persisted_thread_id = thread.id
    user_message = ChatMessage(
        thread_id=persisted_thread_id,
        user_id=user_id,
        role="user",
        content=prompt,
        message_metadata=generation_metadata,
    )
    db.session.add(user_message)
    if thread.title == "New chat":
        thread.title = prompt[:80]
    db.session.flush()
    db.session.add(
        MessageTelemetry(
            message_id=user_message.id,
            user_id=user_id,
            thread_id=persisted_thread_id,
            role=user_message.role,
            request_received_at=request_received_at,
            message_persisted_at=utcnow(),
            completed_at=utcnow(),
            model_name=generation_settings["model_name"],
            reasoning_effort=generation_settings["reasoning_effort"],
            telemetry_metadata={
                "reasoning_provider": generation_settings["reasoning_provider"],
                "mcp_namespaces": namespace_filter,
            },
        )
    )
    db.session.commit()

    return Response(
        stream_with_context(
            _generate_response(
                user_id,
                persisted_thread_id,
                prompt,
                request_received_at,
                generation_settings,
            )
        ),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _generate_response(
    user_id: int,
    thread_id: str,
    prompt: str,
    request_received_at: datetime,
    generation_settings: dict | None = None,
) -> Generator[str, None, None]:
    """Stream agent events and persist the completed assistant message.

    Args:
        user_id: Authenticated user's primary key.
        thread_id: Primary key of the user-owned chat thread.
        prompt: Plain-text user prompt.
        request_received_at: Timestamp captured when the request began.
        generation_settings: Validated settings selected for this response.

    Yields:
        Server-sent event strings for agent progress and completion.
    """
    assistant_text: list[str] = []
    reasoning_text: list[str] = []
    generation_started_at = utcnow()
    first_token_at = None
    token_count = 0
    tool_call_records: list[dict] = []
    try:
        user = db.session.get(User, user_id)
        thread = db.session.get(ChatThread, thread_id)
        if user is None or thread is None or thread.user_id != user_id:
            raise RuntimeError("The chat context is no longer available.")

        effective_settings = generation_settings or _thread_generation_settings(
            thread, user.settings.merged()
        )
        generation_metadata = {
            key: effective_settings[key]
            for key in ("model_name", "reasoning_effort", "reasoning_provider")
        }
        generation_metadata["mcp_namespaces"] = effective_settings.get("mcp_namespaces")
        for event in stream_agent_response(
            user,
            thread,
            prompt,
            settings=effective_settings,
            tool_call_records=tool_call_records,
        ):
            if event["type"] == "token":
                token_count += 1
                if first_token_at is None:
                    first_token_at = utcnow()
                assistant_text.append(event["text"])
            elif event["type"] == "reasoning_summary":
                reasoning_text.append(event["text"])
            yield _sse(event["type"], event)

        assistant_message = ChatMessage(
            thread_id=thread.id,
            user_id=user.id,
            role="assistant",
            content="".join(assistant_text).strip(),
            reasoning_summary="".join(reasoning_text).strip() or None,
            message_metadata=generation_metadata,
        )
        db.session.add(assistant_message)
        db.session.flush()
        assistant_message_id = assistant_message.id
        db.session.add(
            MessageTelemetry(
                message_id=assistant_message_id,
                user_id=user_id,
                thread_id=thread_id,
                role=assistant_message.role,
                request_received_at=request_received_at,
                message_persisted_at=utcnow(),
                generation_started_at=generation_started_at,
                first_token_at=first_token_at,
                completed_at=utcnow(),
                token_count=token_count,
                model_name=effective_settings["model_name"],
                reasoning_effort=effective_settings["reasoning_effort"],
                telemetry_metadata={
                    "reasoning_provider": effective_settings["reasoning_provider"],
                    "mcp_namespaces": effective_settings.get("mcp_namespaces"),
                },
            )
        )
        for record in tool_call_records:
            db.session.add(
                ToolCallTelemetry(
                    message_id=assistant_message_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    **record,
                )
            )
        db.session.commit()
        yield _sse(
            "done",
            {
                "type": "done",
                "message_id": assistant_message_id,
                "thread_id": thread_id,
            },
        )
    except Exception as exc:
        db.session.rollback()
        logger.exception(
            "event=chat.stream.error user_id=%s thread_id=%s error_type=%s",
            user_id,
            thread_id,
            type(exc).__name__,
        )
        yield _sse("error", {"type": "error", "message": str(exc)})


def _user_threads() -> list[ChatThread]:
    """Return the current user's threads in most-recently-updated order."""
    return (
        ChatThread.query.filter_by(user_id=current_user.id)
        .order_by(ChatThread.updated_at.desc())
        .all()
    )


def _create_thread() -> ChatThread:
    """Create and persist an empty thread for the current user.

    Returns:
        The newly persisted thread.
    """
    defaults = current_user.settings.merged()
    thread = ChatThread(
        user_id=current_user.id,
        model_name=defaults["model_name"],
        reasoning_effort=defaults["reasoning_effort"],
        reasoning_provider=defaults["reasoning_provider"],
    )
    db.session.add(thread)
    db.session.commit()
    return thread


def _remember_active_thread(thread: ChatThread) -> None:
    """Remember the current user's selected chat in the browser session.

    Args:
        thread: User-owned chat selected by the current request.
    """
    session[ACTIVE_THREAD_SESSION_KEY] = thread.id


def _thread_generation_settings(thread: ChatThread, user_settings: dict) -> dict:
    """Merge a thread's generation choices over the user's account defaults."""
    settings = dict(user_settings)
    if thread.model_name:
        settings["model_name"] = thread.model_name
    if thread.reasoning_effort:
        settings["reasoning_effort"] = thread.reasoning_effort
    if thread.reasoning_provider:
        settings["reasoning_provider"] = thread.reasoning_provider
    settings["mcp_namespaces"] = thread.mcp_namespace_filter
    return settings


def _render_chat(selected: ChatThread, threads: list[ChatThread]):
    """Render the chat workspace with the selected thread's generation controls."""
    chat_settings = _thread_generation_settings(selected, current_user.settings.merged())
    mcp_namespaces = accessible_mcp_namespaces(current_user.id)
    if chat_settings["mcp_namespaces"] is not None:
        available_names = {item.namespace for item in mcp_namespaces}
        chat_settings["mcp_namespaces"] = sorted(
            set(chat_settings["mcp_namespaces"]) & available_names
        )
    return render_template(
        "chat.html",
        threads=threads,
        selected_thread=selected,
        chat_settings=chat_settings,
        model_options=MODEL_OPTIONS,
        reasoning_options=REASONING_OPTIONS,
        reasoning_provider_options=REASONING_PROVIDER_OPTIONS,
        mcp_namespaces=mcp_namespaces,
    )


def _message_namespace_filter(
    payload: dict,
    thread: ChatThread,
    user_id: int,
) -> list[str] | None:
    """Resolve and validate the MCP namespace filter for one message.

    Args:
        payload: Parsed message request body.
        thread: Chat thread whose current filter is the fallback.
        user_id: Authenticated user whose grants bound the selection.

    Returns:
        ``None`` for all authorized namespaces or a canonical explicit list.

    Raises:
        TypeError: If a request-supplied filter is not a list.
        ValueError: If a filter item is malformed or contains a namespace the
            user cannot currently access.
    """
    authorized = {item.namespace for item in accessible_mcp_namespaces(user_id)}
    if "mcp_namespaces" in payload:
        return _validate_namespace_filter(payload["mcp_namespaces"], authorized)

    stored_filter = thread.mcp_namespace_filter
    if stored_filter is None:
        return None
    if not isinstance(stored_filter, list):
        return []
    return sorted(
        {
            namespace.strip().lower()
            for namespace in stored_filter
            if isinstance(namespace, str) and namespace.strip().lower() in authorized
        }
    )


def _validate_namespace_filter(value: object, authorized: set[str]) -> list[str] | None:
    """Validate a request-supplied namespace filter against current grants.

    Args:
        value: Raw JSON value from the message request.
        authorized: Namespace names the current user may access.

    Returns:
        ``None`` for all namespaces or a sorted, deduplicated explicit list.

    Raises:
        TypeError: If the value is neither ``None`` nor a list.
        ValueError: If a list item is invalid or names an inaccessible namespace.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError({"mcp_namespaces": "Must be null or a list of namespace names."})
    if any(not isinstance(namespace, str) or not namespace.strip() for namespace in value):
        raise ValueError({"mcp_namespaces": "Every namespace must be a non-empty string."})

    normalized = sorted({namespace.strip().lower() for namespace in value})
    unavailable = sorted(set(normalized) - authorized)
    if unavailable:
        names = ", ".join(unavailable)
        raise ValueError({"mcp_namespaces": f"Not available to this account: {names}."})
    return normalized


def _get_thread_or_404(thread_id: str) -> ChatThread:
    """Load a user-owned thread or raise a 404 response.

    Args:
        thread_id: Chat-thread identifier.

    Returns:
        The matching user-owned thread.
    """
    return ChatThread.query.filter_by(id=thread_id, user_id=current_user.id).first_or_404()


def _sse(event: str, data: dict) -> str:
    """Encode an event and payload using the server-sent event format.

    Args:
        event: Event name.
        data: JSON-compatible event payload.

    Returns:
        A complete server-sent event frame.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
