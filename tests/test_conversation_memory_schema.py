from app.extensions import db
from app.models import (
    ChatMessage,
    ChatThread,
    ConversationMemory,
    ConversationMemorySnapshot,
    User,
)


def test_conversation_memory_schema_persists_thread_context(app):
    """Ensure structured conversation memory persists its thread context."""
    with app.app_context():
        user = User(email="memory@example.com")
        user.set_password("very-secure-password")
        thread = ChatThread(user=user, title="Schema design")
        db.session.add(thread)
        db.session.flush()
        message = ChatMessage(
            user_id=user.id,
            thread=thread,
            role="user",
            content="Remember the user is designing conversational memory.",
        )
        snapshot = ConversationMemorySnapshot(
            user=user,
            thread=thread,
            start_message=message,
            end_message=message,
            summary="The user wants thread-scoped conversational memory in Postgres.",
            token_count=14,
            summary_metadata={"model": "test-model"},
        )
        memory = ConversationMemory(
            user=user,
            thread=thread,
            source_message=message,
            snapshot=snapshot,
            memory_type="goal",
            subject="conversational memory",
            content="The user wants conversational memory separate from long-term memory.",
            importance=0.8,
            confidence=0.9,
            memory_metadata={"source": "test"},
        )
        db.session.add(memory)
        db.session.commit()

        saved = ConversationMemory.query.filter_by(thread_id=thread.id, status="active").one()
        assert saved.user.email == "memory@example.com"
        assert saved.thread.title == "Schema design"
        assert saved.source_message.content.startswith("Remember the user")
        assert saved.snapshot.summary.startswith("The user wants")
        assert saved in thread.conversation_memories
        assert snapshot in thread.memory_snapshots
