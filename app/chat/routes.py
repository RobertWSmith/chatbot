from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ChatMessage, ChatThread
from app.services.agent import stream_agent_response

bp = Blueprint("chat", __name__)


@bp.get("/chat")
@login_required
def chat_home():
    threads = _user_threads()
    selected = threads[0] if threads else _create_thread()
    return render_template("chat.html", threads=threads, selected_thread=selected)


@bp.get("/chat/<thread_id>")
@login_required
def chat_thread(thread_id: str):
    selected = _get_thread_or_404(thread_id)
    return render_template("chat.html", threads=_user_threads(), selected_thread=selected)


@bp.post("/api/chat/threads")
@login_required
def create_thread():
    thread = _create_thread()
    return jsonify({"thread": {"id": thread.id, "title": thread.title}}), 201


@bp.post("/api/chat/threads/<thread_id>/messages")
@login_required
def send_message(thread_id: str):
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
    db.session.commit()

    def generate():
        assistant_text: list[str] = []
        reasoning_text: list[str] = []
        try:
            for event in stream_agent_response(current_user, thread, prompt):
                if event["type"] == "token":
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
            )
            db.session.add(assistant_message)
            db.session.commit()
            yield _sse(
                "done",
                {
                    "type": "done",
                    "message_id": assistant_message.id,
                    "thread_id": thread.id,
                },
            )
        except Exception as exc:
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
    thread = ChatThread(user_id=current_user.id)
    db.session.add(thread)
    db.session.commit()
    return thread


def _get_thread_or_404(thread_id: str) -> ChatThread:
    return ChatThread.query.filter_by(id=thread_id, user_id=current_user.id).first_or_404()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
