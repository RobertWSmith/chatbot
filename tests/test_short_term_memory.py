from app.extensions import db
from app.models import ChatMessage, ChatThread, ConversationMemorySnapshot, User
from app.services.agent import _conversation_messages


def test_conversation_messages_loads_recent_thread_turns_from_postgres(app):
    with app.app_context():
        user = User(email="history@example.com")
        user.set_password("very-secure-password")
        thread = ChatThread(user=user)
        db.session.add(thread)
        db.session.flush()
        first = ChatMessage(
            user_id=user.id,
            thread_id=thread.id,
            role="user",
            content="My favorite color is cerulean.",
        )
        second = ChatMessage(
            user_id=user.id,
            thread_id=thread.id,
            role="assistant",
            content="Got it. Cerulean.",
        )
        current = ChatMessage(
            user_id=user.id,
            thread_id=thread.id,
            role="user",
            content="What color did I mention?",
        )
        db.session.add_all([first, second, current])
        db.session.commit()

        messages = _conversation_messages(user, thread, "What color did I mention?")

        assert messages == [
            {"role": "user", "content": "My favorite color is cerulean."},
            {"role": "assistant", "content": "Got it. Cerulean."},
            {"role": "user", "content": "What color did I mention?"},
        ]


def test_conversation_messages_respects_history_limit(app):
    app.config["CONVERSATION_HISTORY_LIMIT"] = 2
    with app.app_context():
        user = User(email="limited@example.com")
        user.set_password("very-secure-password")
        thread = ChatThread(user=user)
        db.session.add(thread)
        db.session.flush()
        for index in range(5):
            db.session.add(
                ChatMessage(
                    user_id=user.id,
                    thread_id=thread.id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"turn {index}",
                )
            )
        db.session.commit()

        messages = _conversation_messages(user, thread, "turn 4")

        assert messages == [
            {
                "role": "system",
                "content": (
                    "Rolling summary of earlier turns in this same chat. Use it as short-term "
                    "conversation memory before the recent verbatim turns:\n\nConversation summary:\n"
                    "- user: turn 0\n- assistant: turn 1\n- user: turn 2"
                ),
            },
            {"role": "assistant", "content": "turn 3"},
            {"role": "user", "content": "turn 4"},
        ]


def test_conversation_messages_rolls_overflow_into_snapshot(app):
    app.config["CONVERSATION_HISTORY_LIMIT"] = 3
    with app.app_context():
        user = User(email="summary@example.com")
        user.set_password("very-secure-password")
        thread = ChatThread(user=user)
        db.session.add(thread)
        db.session.flush()
        for index in range(6):
            db.session.add(
                ChatMessage(
                    user_id=user.id,
                    thread_id=thread.id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"important detail {index}",
                )
            )
        db.session.commit()

        messages = _conversation_messages(user, thread, "important detail 5")
        snapshot = ConversationMemorySnapshot.query.filter_by(thread_id=thread.id).one()

        assert snapshot.message_start_id is not None
        assert snapshot.message_end_id is not None
        assert "important detail 0" in snapshot.summary
        assert "important detail 2" in snapshot.summary
        assert messages[0]["role"] == "system"
        assert "important detail 0" in messages[0]["content"]
        assert messages[-3:] == [
            {"role": "assistant", "content": "important detail 3"},
            {"role": "user", "content": "important detail 4"},
            {"role": "assistant", "content": "important detail 5"},
            {"role": "user", "content": "important detail 5"},
        ][-3:]


def test_conversation_messages_extends_existing_snapshot(app):
    app.config["CONVERSATION_HISTORY_LIMIT"] = 2
    with app.app_context():
        user = User(email="extend-summary@example.com")
        user.set_password("very-secure-password")
        thread = ChatThread(user=user)
        db.session.add(thread)
        db.session.flush()
        for index in range(4):
            db.session.add(
                ChatMessage(
                    user_id=user.id,
                    thread_id=thread.id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"phase one {index}",
                )
            )
        db.session.commit()
        first_messages = _conversation_messages(user, thread, "phase one 3")
        assert "phase one 0" in first_messages[0]["content"]

        db.session.add(
            ChatMessage(
                user_id=user.id,
                thread_id=thread.id,
                role="user",
                content="phase two 4",
            )
        )
        db.session.commit()

        second_messages = _conversation_messages(user, thread, "phase two 4")
        snapshots = ConversationMemorySnapshot.query.filter_by(thread_id=thread.id).all()

        assert len(snapshots) == 2
        assert "Earlier summary" in second_messages[0]["content"]
        assert "phase one 0" in second_messages[0]["content"]
        assert "phase one 2" in second_messages[0]["content"]
