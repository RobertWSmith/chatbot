import app.chat.routes as chat_routes
from app.extensions import db
from app.models import ChatMessage, ChatThread
from app.services import agent as agent_service

from .conftest import register


def test_new_chat_inherits_generation_defaults_and_renders_compact_controls(client, app):
    register(client)
    response = client.patch(
        "/api/settings",
        json={
            "model_name": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "reasoning_provider": "langgraph",
        },
    )
    assert response.status_code == 200

    thread_response = client.post("/api/chat/threads")
    thread = thread_response.get_json()["thread"]
    assert thread["model_name"] == "gpt-5.6-luna"
    assert thread["reasoning_effort"] == "max"
    assert thread["reasoning_provider"] == "langgraph"

    page = client.get(f"/chat/{thread['id']}")
    assert page.status_code == 200
    assert b'class="generation-control"' in page.data
    assert b'id="reasoning-provider-select"' in page.data
    assert b'id="model-select"' in page.data
    assert b'id="reasoning-select"' in page.data
    assert b'value="gpt-5.6-luna" selected' in page.data
    assert b'value="max" selected' in page.data
    assert b'value="langgraph" selected' in page.data

    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert f'value="{model}"'.encode() in page.data
    for effort in ("none", "low", "medium", "high", "xhigh", "max"):
        assert f'value="{effort}"'.encode() in page.data
    assert b'value="minimal"' not in page.data

    settings_page = client.get("/settings")
    assert b'name="model_name"' in settings_page.data
    assert b'name="reasoning_effort"' in settings_page.data
    assert b'name="reasoning_provider"' in settings_page.data
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert f'value="{model}"'.encode() in settings_page.data
    for effort in ("none", "low", "medium", "high", "xhigh", "max"):
        assert f'value="{effort}"'.encode() in settings_page.data

    with app.app_context():
        persisted = db.session.get(ChatThread, thread["id"])
        assert persisted.model_name == "gpt-5.6-luna"
        assert persisted.reasoning_effort == "max"
        assert persisted.reasoning_provider == "langgraph"


def test_chat_generation_choices_are_thread_scoped_and_logged(client, app, monkeypatch):
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    captured = {}

    def fake_stream(user, thread, prompt):
        captured.update(
            model_name=thread.model_name,
            reasoning_effort=thread.reasoning_effort,
            reasoning_provider=thread.reasoning_provider,
        )
        yield {"type": "token", "text": "Done."}

    monkeypatch.setattr(chat_routes, "stream_agent_response", fake_stream)
    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={
            "message": "Use these chat controls",
            "model_name": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "reasoning_provider": "langgraph",
        },
    )

    assert response.status_code == 200
    assert b"event: done" in response.data
    assert captured == {
        "model_name": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "reasoning_provider": "langgraph",
    }

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        messages = ChatMessage.query.filter_by(thread_id=thread_id).order_by(ChatMessage.id).all()
        assert thread.model_name == "gpt-5.6-sol"
        assert thread.reasoning_effort == "xhigh"
        assert thread.reasoning_provider == "langgraph"
        assert [message.telemetry.model_name for message in messages] == [
            "gpt-5.6-sol",
            "gpt-5.6-sol",
        ]
        assert [message.telemetry.reasoning_effort for message in messages] == [
            "xhigh",
            "xhigh",
        ]
        assert all(
            message.telemetry.telemetry_metadata["reasoning_provider"] == "langgraph"
            for message in messages
        )


def test_invalid_chat_generation_choice_is_rejected_before_persistence(client, app):
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


def test_gpt_5_6_reasoning_workflows_include_none_and_max():
    assert agent_service._reasoning_workflow_for_effort("none") == ["answer"]
    assert agent_service._reasoning_workflow_for_effort(
        "max"
    ) == agent_service._reasoning_workflow_for_effort("xhigh")
