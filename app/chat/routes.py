from __future__ import annotations

import json

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ChatMessage, ChatThread, MessageTelemetry, utcnow
from app.services.agent import stream_agent_response
from app.validation import REASONING_PROVIDER_OPTIONS, validate_settings_update

bp = Blueprint("chat", __name__)


@bp.get("/chat")
@login_required
def chat_home():
    threads = _user_threads()
    selected = threads[0] if threads else _create_thread()
    return _render_chat(selected, threads)


@bp.get("/chat/<thread_id>")
@login_required
def chat_thread(thread_id: str):
    selected = _get_thread_or_404(thread_id)
    return _render_chat(selected, _user_threads())


@bp.post("/api/chat/threads")
@login_required
def create_thread():
    thread = _create_thread()
    return (
        jsonify(
            {
                "thread": {
                    "id": thread.id,
                    "title": thread.title,
                    "reasoning_provider": thread.reasoning_provider,
                }
            }
        ),
        201,
    )


@bp.post("/api/chat/threads/<thread_id>/messages")
@login_required
def send_message(thread_id: str):
    request_received_at = utcnow()
    thread = _get_thread_or_404(thread_id)
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        return jsonify({"error": "Message is required."}), 400

    account_settings = current_user.settings.merged()
    reasoning_provider = (
        thread.reasoning_provider or account_settings["reasoning_provider"]
    )
    if "reasoning_provider" in payload:
        try:
            validated = validate_settings_update(
                account_settings,
                {"reasoning_provider": payload["reasoning_provider"]},
            )
        except ValueError as exc:
            return jsonify({"errors": exc.args[0]}), 400
        reasoning_provider = validated["reasoning_provider"]
    thread.reasoning_provider = reasoning_provider

    user_message = ChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="user",
        content=prompt,
        message_metadata={"reasoning_provider": reasoning_provider},
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

    def generate():
        assistant_text: list[str] = []
        reasoning_text: list[str] = []
        generation_started_at = utcnow()
        first_token_at = None
        token_count = 0
        try:
            for event in stream_agent_response(current_user, thread, prompt):
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
                user_id=current_user.id,
                role="assistant",
                content="".join(assistant_text).strip(),
                reasoning_summary="".join(reasoning_text).strip() or None,
                message_metadata={"reasoning_provider": reasoning_provider},
            )
            db.session.add(assistant_message)
            db.session.flush()
            db.session.add(
                MessageTelemetry(
                    message_id=assistant_message.id,
                    user_id=current_user.id,
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
        except Exception as exc:  # noqa: BLE001 - convert stream failures to SSE errors
            db.session.rollback()
            yield _sse("error", {"type": "error", "message": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _user_threads():
    return (
        ChatThread.query.filter_by(user_id=current_user.id)
        .order_by(ChatThread.updated_at.desc())
        .all()
    )


def _create_thread() -> ChatThread:
    thread = ChatThread(
        user_id=current_user.id,
        reasoning_provider=current_user.settings.merged()["reasoning_provider"],
    )
    db.session.add(thread)
    db.session.commit()
    return thread


def _render_chat(selected: ChatThread, threads: list[ChatThread]):
    reasoning_provider = (
        selected.reasoning_provider
        or current_user.settings.merged()["reasoning_provider"]
    )
    return render_template(
        "chat.html",
        threads=threads,
        selected_thread=selected,
        reasoning_provider=reasoning_provider,
        reasoning_provider_options=REASONING_PROVIDER_OPTIONS,
    )


def _get_thread_or_404(thread_id: str) -> ChatThread:
    return ChatThread.query.filter_by(
        id=thread_id, user_id=current_user.id
    ).first_or_404()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
