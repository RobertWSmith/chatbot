from __future__ import annotations

from app.extensions import db
from app.models import ChatMessage, ChatThread, ConversationMemorySnapshot, User


def summarize_overflowing_conversation(
    user: User,
    thread: ChatThread,
    messages: list[ChatMessage],
    keep_count: int,
) -> ConversationMemorySnapshot | None:
    """Roll messages outside the recent-history window into a snapshot.

    Args:
        user: Owner of the conversation.
        thread: Thread whose messages are being compacted.
        messages: Chronologically ordered conversation messages.
        keep_count: Number of recent messages to preserve verbatim.

    Returns:
        The latest snapshot, or ``None`` when the thread has no snapshot and
        does not require one.
    """
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
    """Return the newest conversation snapshot for a user-owned thread.

    Args:
        user_id: Owner's primary key.
        thread_id: Chat-thread identifier.

    Returns:
        The newest matching snapshot, or ``None``.
    """
    return (
        ConversationMemorySnapshot.query.filter_by(user_id=user_id, thread_id=thread_id)
        .order_by(ConversationMemorySnapshot.created_at.desc())
        .first()
    )


def build_summary(prior_summary: str, messages: list[ChatMessage]) -> str:
    """Build a deterministic rolling summary from earlier messages.

    Args:
        prior_summary: Existing rolling summary, if any.
        messages: Newly overflowing messages in chronological order.

    Returns:
        A compact plain-text conversation summary.
    """
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
    """Convert a snapshot into a system message for the chat model.

    Args:
        snapshot: Rolling conversation snapshot.

    Returns:
        A model-compatible system message, or ``None`` without a snapshot.
    """
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
    """Estimate token count using whitespace-delimited words.

    Args:
        text: Text to estimate.

    Returns:
        A positive approximate token count.
    """
    return max(1, len(text.split()))
