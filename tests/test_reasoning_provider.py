from types import SimpleNamespace

import app.chat.routes as chat_routes
from app.extensions import db
from app.models import ChatMessage, ChatThread, User
from app.services import agent as agent_service

from .conftest import register


class FakeChatModel:
    def invoke(self, messages):
        prompt = messages[-1]["content"]
        if "Plan the answer" in prompt:
            return SimpleNamespace(
                content="Use the available context, then answer directly."
            )
        return SimpleNamespace(content="Answer from the LangGraph workflow.")


def test_new_chat_inherits_account_reasoning_provider(client, app):
    register(client)
    response = client.patch("/api/settings", json={"reasoning_provider": "langgraph"})
    assert response.status_code == 200

    thread_response = client.post("/api/chat/threads")
    thread = thread_response.get_json()["thread"]
    assert thread["reasoning_provider"] == "langgraph"

    page = client.get(f"/chat/{thread['id']}")
    assert page.status_code == 200
    assert b'id="reasoning-provider-select"' in page.data
    assert b'value="langgraph" selected' in page.data
    assert b">OpenAI</option>" in page.data
    assert b">LangGraph</option>" in page.data

    with app.app_context():
        persisted = db.session.get(ChatThread, thread["id"])
        assert persisted.reasoning_provider == "langgraph"


def test_chat_reasoning_provider_is_validated_and_persisted(client, app, monkeypatch):
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    captured = {}

    def fake_stream(user, thread, prompt):
        captured["reasoning_provider"] = thread.reasoning_provider
        yield {"type": "token", "text": "Done."}

    monkeypatch.setattr(chat_routes, "stream_agent_response", fake_stream)

    invalid = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "Hello", "reasoning_provider": "unknown"},
    )
    assert invalid.status_code == 400
    assert "reasoning_provider" in invalid.get_json()["errors"]

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"message": "Hello", "reasoning_provider": "langgraph"},
    )
    assert response.status_code == 200
    assert b"event: done" in response.data
    assert captured["reasoning_provider"] == "langgraph"

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        messages = ChatMessage.query.filter_by(thread_id=thread_id).all()
        assert thread.reasoning_provider == "langgraph"
        assert {
            message.message_metadata["reasoning_provider"] for message in messages
        } == {"langgraph"}


def test_agent_routes_each_reasoning_provider(client, app, monkeypatch):
    app.config["OPENAI_API_KEY"] = "test-key"
    register(client)
    calls = []

    def fake_openai(user, thread, messages, settings):
        calls.append(("openai", settings["reasoning_provider"]))
        yield {"type": "token", "text": "OpenAI"}

    def fake_langgraph(user, thread, messages, settings):
        calls.append(("langgraph", settings["reasoning_provider"]))
        yield {"type": "token", "text": "LangGraph"}

    monkeypatch.setattr(agent_service, "_stream_langgraph_response", fake_openai)
    monkeypatch.setattr(
        agent_service,
        "_stream_custom_reasoning_graph_response",
        fake_langgraph,
    )

    with app.app_context():
        user = User.query.one()
        thread = ChatThread(user_id=user.id, reasoning_provider="openai")
        db.session.add(thread)
        db.session.commit()
        assert list(agent_service.stream_agent_response(user, thread, "First"))[-1][
            "text"
        ] == ("OpenAI")

        thread.reasoning_provider = "langgraph"
        db.session.commit()
        assert list(agent_service.stream_agent_response(user, thread, "Second"))[-1][
            "text"
        ] == ("LangGraph")

    assert calls == [("openai", "openai"), ("langgraph", "langgraph")]


def test_langgraph_provider_streams_plan_and_answer(client, app, monkeypatch):
    app.config["OPENAI_API_KEY"] = "test-key"
    register(client)
    monkeypatch.setattr(
        agent_service,
        "_build_chat_model",
        lambda settings, **kwargs: FakeChatModel(),
    )
    monkeypatch.setattr(agent_service, "_safe_memory_context", lambda user, query: "")

    with app.app_context():
        user = User.query.one()
        thread = ChatThread(user_id=user.id, reasoning_provider="langgraph")
        db.session.add(thread)
        db.session.commit()
        events = list(
            agent_service.stream_agent_response(user, thread, "How does this work?")
        )

    assert {"type": "status", "text": "Running LangGraph reasoning workflow"} in events
    assert any(
        event["type"] == "reasoning_summary" and "Plan:" in event["text"]
        for event in events
    )
    assert {"type": "token", "text": "Answer from the LangGraph workflow."} in events
