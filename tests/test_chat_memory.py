from app.chat.routes import _generate_response
from app.extensions import db
from app.models import ChatMessage, ChatThread, MessageTelemetry, PendingMemory, utcnow

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


def test_stream_reloads_entities_after_original_session_is_removed(client, app):
    """Ensure deferred streaming does not depend on request-scoped ORM instances."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        user_id = thread.user_id
        db.session.remove()

        body = "".join(
            _generate_response(
                user_id,
                thread_id,
                "Generate a response after the request session closes.",
                utcnow(),
            )
        )

        assert "event: token" in body
        assert "event: done" in body
        assert "not bound to a Session" not in body
        assert ChatMessage.query.filter_by(thread_id=thread_id, role="assistant").count() == 1


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


def test_chat_home_restores_the_last_selected_thread(client):
    """Ensure leaving Chat does not discard the explicitly selected thread."""
    register(client)
    first_thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    client.post("/api/chat/threads")

    selected = client.get(f"/chat/{first_thread_id}")
    assert f'data-thread-id="{first_thread_id}"'.encode() in selected.data

    client.get("/memory")
    restored = client.get("/chat")

    assert restored.status_code == 200
    assert f'data-thread-id="{first_thread_id}"'.encode() in restored.data


def test_sending_a_message_refreshes_thread_recency(client, app):
    """Ensure an active conversation moves to the top of the thread list."""
    register(client)
    first_thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    second_thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    with app.app_context():
        first_thread = db.session.get(ChatThread, first_thread_id)
        second_thread = db.session.get(ChatThread, second_thread_id)
        first_thread.title = "Older thread"
        second_thread.title = "Newer thread"
        first_thread.updated_at = utcnow().replace(year=2024)
        second_thread.updated_at = utcnow().replace(year=2025)
        db.session.commit()

    response = client.post(
        f"/api/chat/threads/{first_thread_id}/messages",
        json={"message": "Make this the active conversation"},
    )

    assert response.status_code == 200
    with app.app_context():
        most_recent = (
            ChatThread.query.order_by(ChatThread.updated_at.desc())
            .with_entities(ChatThread.id)
            .first()
        )
        assert most_recent == (first_thread_id,)


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
