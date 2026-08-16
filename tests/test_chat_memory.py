from app.extensions import db
from app.models import ChatMessage, ChatThread, MessageTelemetry, PendingMemory

from .conftest import register


def test_chat_stream_persists_messages_and_memory_proposal(client, app):
    """Ensure demo streaming persists both messages and a memory proposal."""
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


def test_demo_response_respects_disabled_memory_proposals(client, app):
    """Ensure demo mode honors the user's memory-proposal privacy setting."""
    register(client)
    settings_response = client.patch(
        "/api/settings",
        json={"privacy": {"allow_memory_proposals": False}},
    )
    assert settings_response.status_code == 200
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "please remember that privacy comes first"},
    )

    assert response.status_code == 200
    assert b"event: memory_proposal" not in response.data
    with app.app_context():
        assert PendingMemory.query.count() == 0


def test_cross_user_thread_access_is_blocked(client):
    """Ensure a user cannot access another user's chat thread."""
    register(client, email="a@example.com")
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    client.get("/logout")

    register(client, email="b@example.com")
    response = client.get(f"/chat/{thread_id}")
    assert response.status_code == 404


def test_saved_reasoning_summary_is_marked_for_markdown_rendering(client, app):
    """Ensure persisted reasoning summaries render through the Markdown path."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        db.session.add(
            ChatMessage(
                thread_id=thread_id,
                user_id=thread.user_id,
                role="assistant",
                content="Done.",
                reasoning_summary="**Checked**\n\n- First item",
            )
        )
        db.session.commit()

    response = client.get(f"/chat/{thread_id}")

    assert response.status_code == 200
    assert b'class="reasoning"' in response.data
    assert b'<details class="reasoning" open>' not in response.data
    assert b'data-markdown-source="**Checked**' in response.data


def test_demo_response_does_not_echo_the_user_prompt(client):
    """Ensure demo output does not reflect arbitrary prompt content."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    prompt = "unique prompt that should only appear in the user message"

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": prompt},
    )

    assert response.status_code == 200
    assert prompt.encode() not in response.data
    assert b"Your message was" not in response.data


def test_demo_response_does_not_repeat_prior_messages(client, app):
    """Ensure demo output does not leak previous conversation content."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    prior_response = "PRIOR_RESPONSE_SENTINEL"

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        db.session.add(
            ChatMessage(
                thread_id=thread_id,
                user_id=thread.user_id,
                role="assistant",
                content=prior_response,
            )
        )
        db.session.commit()

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "Give me a fresh response"},
    )

    assert response.status_code == 200
    assert prior_response.encode() not in response.data
    assert b"Recent Postgres conversation context" not in response.data
