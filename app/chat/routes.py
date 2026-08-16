from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ChatMessage, ChatThread, MessageTelemetry, User, utcnow
from app.services.agent import stream_agent_response

bp = Blueprint("chat", __name__)


@bp.get("/chat")
@login_required
def chat_home():
    """Render the most recently updated chat, creating one when needed."""
    threads = _user_threads()
    selected = threads[0] if threads else _create_thread()
    return render_template("chat.html", threads=threads, selected_thread=selected)


@bp.get("/chat/<thread_id>")
@login_required
def chat_thread(thread_id: str):
    """Render a user-owned chat thread.

    Args:
        thread_id: Chat-thread identifier from the URL.
    """
    selected = _get_thread_or_404(thread_id)
    return render_template("chat.html", threads=_user_threads(), selected_thread=selected)


@bp.post("/api/chat/threads")
@login_required
def create_thread():
    """Create an empty chat thread for the current user."""
    thread = _create_thread()
    return jsonify({"thread": {"id": thread.id, "title": thread.title}}), 201


@bp.post("/api/chat/threads/<thread_id>/messages")
@login_required
def send_message(thread_id: str):
    """Persist a user message and stream the assistant response.

    Args:
        thread_id: User-owned chat thread receiving the message.
    """
    request_received_at = utcnow()
    thread = _get_thread_or_404(thread_id)
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        return jsonify({"error": "Message is required."}), 400

    user_message = ChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="user",
        content=prompt,
    )
    db.session.add(user_message)
    if thread.title == "New chat":
        thread.title = prompt[:80]
    db.session.flush()
    db.session.add(
        MessageTelemetry(
            message_id=user_message.id,
            user_id=current_user.id,
            thread_id=thread.id,
            role=user_message.role,
            request_received_at=request_received_at,
            message_persisted_at=utcnow(),
            completed_at=utcnow(),
        )
    )
    db.session.commit()

    return Response(
        stream_with_context(
            _generate_response(
                current_user._get_current_object(),
                thread,
                prompt,
                request_received_at,
            )
        ),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _generate_response(
    user: User,
    thread: ChatThread,
    prompt: str,
    request_received_at: datetime,
) -> Generator[str, None, None]:
    """Stream agent events and persist the completed assistant message.

    Args:
        user: Authenticated user sending the message.
        thread: Chat thread receiving the response.
        prompt: Plain-text user prompt.
        request_received_at: Timestamp captured when the request began.

    Yields:
        Server-sent event strings for agent progress and completion.
    """
    assistant_text: list[str] = []
    reasoning_text: list[str] = []
    generation_started_at = utcnow()
    first_token_at = None
    token_count = 0
    try:
        for event in stream_agent_response(user, thread, prompt):
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
        )
        db.session.add(assistant_message)
        db.session.flush()
        db.session.add(
            MessageTelemetry(
                message_id=assistant_message.id,
                user_id=user.id,
                thread_id=thread.id,
                role=assistant_message.role,
                request_received_at=request_received_at,
                message_persisted_at=utcnow(),
                generation_started_at=generation_started_at,
                first_token_at=first_token_at,
                completed_at=utcnow(),
                token_count=token_count,
            )
        )
        db.session.commit()
        yield _sse(
            "done",
            {
                "type": "done",
                "message_id": assistant_message.id,
                "thread_id": thread.id,
            },
        )
    except Exception as exc:  # noqa: BLE001 - stream failures must become SSE error events.
        db.session.rollback()
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
    thread = ChatThread(user_id=current_user.id)
    db.session.add(thread)
    db.session.commit()
    return thread


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
