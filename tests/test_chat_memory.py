from app.extensions import db
from app.models import ChatMessage, ChatThread, MessageTelemetry, PendingMemory

from .conftest import register


def test_chat_stream_persists_messages_and_memory_proposal(client, app):
    register(client)
    thread_response = client.post("/api/chat/threads")
    thread_id = thread_response.get_json()["thread"]["id"]

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "please remember I like concise answers"},
    )

    assert response.status_code == 200
    body = response.data.decode()
    assert "event: token" in body
    assert "event: memory_proposal" in body
    assert "event: done" in body

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        assert thread is not None
        user_message = ChatMessage.query.filter_by(thread_id=thread_id, role="user").one()
        assistant_message = ChatMessage.query.filter_by(thread_id=thread_id, role="assistant").one()
        assert user_message.telemetry is not None
        assert assistant_message.telemetry is not None
        assert MessageTelemetry.query.filter_by(thread_id=thread_id).count() == 2
        assert user_message.telemetry.first_token_at is None
        assert assistant_message.telemetry.generation_started_at is not None
        assert assistant_message.telemetry.first_token_at is not None
        assert assistant_message.telemetry.token_count > 0
        assert (
            assistant_message.telemetry.generation_started_at
            <= assistant_message.telemetry.first_token_at
            <= assistant_message.telemetry.completed_at
        )
        assert PendingMemory.query.filter_by(user_id=thread.user_id, status="pending").count() == 1


def test_cross_user_thread_access_is_blocked(client):
    register(client, email="a@example.com")
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    client.get("/logout")

    register(client, email="b@example.com")
    response = client.get(f"/chat/{thread_id}")
    assert response.status_code == 404
