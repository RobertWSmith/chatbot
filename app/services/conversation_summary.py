from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import ChatMessage, ChatThread, ConversationMemorySnapshot, User


def summarize_overflowing_conversation(
    user: User,
    thread: ChatThread,
    messages: list[ChatMessage],
    keep_count: int,
) -> ConversationMemorySnapshot | None:
    if len(messages) <= keep_count:
        return latest_snapshot(user.id, thread.id)

    latest = latest_snapshot(user.id, thread.id)
    summarized_through_id = latest.message_end_id if latest else None
    overflow = messages[: max(0, len(messages) - keep_count)]
    unsummarized = [
        message
        for message in overflow
        if summarized_through_id is None or message.id > summarized_through_id
    ]
    if not unsummarized:
        return latest

    prior_summary = latest.summary if latest else ""
    summary = build_summary(prior_summary, unsummarized)
    snapshot = ConversationMemorySnapshot(
        user_id=user.id,
        thread_id=thread.id,
        message_start_id=unsummarized[0].id,
        message_end_id=unsummarized[-1].id,
        summary=summary,
        token_count=estimate_token_count(summary),
        summary_metadata={
            "strategy": "rolling_local_summary",
            "message_count": len(unsummarized),
            "previous_snapshot_id": latest.id if latest else None,
        },
    )
    db.session.add(snapshot)
    db.session.commit()
    return snapshot


def latest_snapshot(user_id: int, thread_id: str) -> ConversationMemorySnapshot | None:
    return (
        ConversationMemorySnapshot.query.filter_by(user_id=user_id, thread_id=thread_id)
        .order_by(ConversationMemorySnapshot.created_at.desc())
        .first()
    )


def build_summary(prior_summary: str, messages: list[ChatMessage]) -> str:
    lines = []
    if prior_summary:
        lines.append("Earlier summary:")
        lines.append(prior_summary.strip())
        lines.append("")
        lines.append("Newly summarized turns:")
    else:
        lines.append("Conversation summary:")

    for message in messages:
        content = " ".join(message.content.split())
        if len(content) > 700:
            content = f"{content[:697]}..."
        lines.append(f"- {message.role}: {content}")
    return "\n".join(lines).strip()


def snapshot_context_message(snapshot: ConversationMemorySnapshot | None) -> dict[str, str] | None:
    if not snapshot:
        return None
    return {
        "role": "system",
        "content": (
            "Rolling summary of earlier turns in this same chat. Use it as short-term "
            f"conversation memory before the recent verbatim turns:\n\n{snapshot.summary}"
        ),
    }


def estimate_token_count(text: str) -> int:
    return max(1, len(text.split()))
