from uuid import uuid4

import app.chat.routes as chat_routes
from app.extensions import db
from app.models import ChatMessage, ChatThread, ToolCallTelemetry, User, utcnow

from .conftest import register


def test_new_chats_inherit_account_generation_defaults(client, app):
    """Ensure account defaults seed new chats and their inline selectors."""
    register(client)
    response = client.patch(
        "/api/settings",
        json={"model_name": "gpt-5.6-luna", "reasoning_effort": "high"},
    )
    assert response.status_code == 200

    thread_response = client.post("/api/chat/threads")
    thread_payload = thread_response.get_json()["thread"]
    assert thread_payload["model_name"] == "gpt-5.6-luna"
    assert thread_payload["reasoning_effort"] == "high"

    page = client.get(f"/chat/{thread_payload['id']}")
    assert page.status_code == 200
    assert b'id="model-select"' in page.data
    assert b'value="gpt-5.6-luna" selected' in page.data
    assert b'id="reasoning-select"' in page.data
    assert b'value="high" selected' in page.data

    with app.app_context():
        thread = db.session.get(ChatThread, thread_payload["id"])
        assert thread.model_name == "gpt-5.6-luna"
        assert thread.reasoning_effort == "high"


def test_chat_choices_are_thread_scoped_and_logged_per_turn(client, app, monkeypatch):
    """Ensure one send uses and audits its thread-specific generation choices."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    with app.app_context():
        account_default_model = User.query.one().settings.merged()["model_name"]
    captured = {}

    def fake_stream(user, thread, prompt, *, settings, tool_call_records):
        """Capture selected settings and emulate one successful tool-assisted turn."""
        captured.update(settings=settings, prompt=prompt)
        now = utcnow()
        tool_call_records.append(
            {
                "run_id": str(uuid4()),
                "parent_run_id": None,
                "tool_name": "mcp_portal_search",
                "source": "mcp:portal",
                "input_summary": "keys:query",
                "status": "succeeded",
                "duration_ms": 12,
                "output_type": "str",
                "output_chars": 42,
                "error_type": None,
                "started_at": now,
                "completed_at": now,
            }
        )
        yield {"type": "token", "text": "Done."}

    monkeypatch.setattr(chat_routes, "stream_agent_response", fake_stream)
    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={
            "message": "Use the selected model",
            "model_name": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
        },
    )

    assert response.status_code == 200
    assert b"event: done" in response.data
    assert captured["settings"]["model_name"] == "gpt-5.6-sol"
    assert captured["settings"]["reasoning_effort"] == "xhigh"

    with app.app_context():
        user = User.query.one()
        thread = db.session.get(ChatThread, thread_id)
        messages = ChatMessage.query.filter_by(thread_id=thread_id).order_by(ChatMessage.id).all()
        tool_call = ToolCallTelemetry.query.one()

        assert user.settings.merged()["model_name"] == account_default_model
        assert thread.model_name == "gpt-5.6-sol"
        assert thread.reasoning_effort == "xhigh"
        assert [message.telemetry.model_name for message in messages] == [
            "gpt-5.6-sol",
            "gpt-5.6-sol",
        ]
        assert [message.telemetry.reasoning_effort for message in messages] == [
            "xhigh",
            "xhigh",
        ]
        assert tool_call.message_id == messages[1].id
        assert tool_call.tool_name == "mcp_portal_search"
        assert tool_call.input_summary == "keys:query"
        assert tool_call.status == "succeeded"


def test_invalid_chat_generation_choice_is_rejected_before_persistence(client, app):
    """Ensure unsupported composer values cannot create an unaudited turn."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "Hello", "model_name": "unknown-model"},
    )

    assert response.status_code == 400
    assert "model_name" in response.get_json()["errors"]
    with app.app_context():
        assert ChatMessage.query.filter_by(thread_id=thread_id).count() == 0
