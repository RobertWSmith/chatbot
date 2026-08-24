import app.chat.routes as chat_routes
from app.extensions import db
from app.models import ChatMessage, ChatThread, User
from app.services import agent as agent_service

from .conftest import register


def test_new_chat_inherits_account_reasoning_provider(client, app):
    """Ensure new chats inherit and render the account's provider default."""
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
    """Ensure chat-level provider choices are validated and audited."""
    register(client)
    thread_id = client.post("/api/chat/threads").get_json()["thread"]["id"]
    captured = {}

    def fake_stream(user, thread, prompt, *, settings, tool_call_records):
        """Capture the persisted provider through the current stream contract."""
        captured["reasoning_provider"] = thread.reasoning_provider
        captured["settings"] = settings
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
        assert {message.message_metadata["reasoning_provider"] for message in messages} == {
            "langgraph"
        }


def test_agent_routes_each_reasoning_provider(client, app, monkeypatch):
    """Ensure each per-chat provider selects the corresponding agent workflow."""
    app.config["OPENAI_API_KEY"] = "test-key"
    register(client)
    calls = []

    def fake_openai(user, thread, messages, settings, tool_call_records):
        """Record use of the provider-native prebuilt-agent path."""
        calls.append(("openai", settings["reasoning_provider"]))
        yield {"type": "token", "text": "OpenAI"}

    def fake_langgraph(user, thread, messages, settings, tool_call_records):
        """Record use of the app-owned custom LangGraph path."""
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
        assert list(agent_service.stream_agent_response(user, thread, "First"))[-1]["text"] == (
            "OpenAI"
        )

        thread.reasoning_provider = "langgraph"
        db.session.commit()
        assert list(agent_service.stream_agent_response(user, thread, "Second"))[-1]["text"] == (
            "LangGraph"
        )

    assert calls == [("openai", "openai"), ("langgraph", "langgraph")]


def test_tool_capable_models_use_responses_api_without_forcing_provider_reasoning(app, monkeypatch):
    """Ensure custom MCP research can call tools without provider reasoning."""
    captured_kwargs = []

    class FakeOpenAIModel:
        """Capture keyword arguments passed to the OpenAI chat-model adapter."""

        def __init__(self, **kwargs):
            """Record one model construction."""
            captured_kwargs.append(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeOpenAIModel)
    settings = {
        "model_name": "gpt-5.4",
        "reasoning_effort": "medium",
        "reasoning_summaries_enabled": True,
    }

    with app.app_context():
        agent_service._build_chat_model(settings, provider_reasoning=False)
        agent_service._build_chat_model(
            settings,
            provider_reasoning=False,
            tool_capable=True,
        )
        agent_service._build_chat_model(
            settings,
            provider_reasoning=True,
            tool_capable=True,
        )

    custom_graph_kwargs, research_agent_kwargs, provider_agent_kwargs = captured_kwargs
    assert "model_kwargs" not in custom_graph_kwargs
    assert "reasoning" not in custom_graph_kwargs
    assert "use_responses_api" not in custom_graph_kwargs
    assert research_agent_kwargs["use_responses_api"] is True
    assert "model_kwargs" not in research_agent_kwargs
    assert "reasoning" not in research_agent_kwargs
    assert provider_agent_kwargs["use_responses_api"] is True
    assert provider_agent_kwargs["model_kwargs"] == {"parallel_tool_calls": True}
    assert provider_agent_kwargs["reasoning"] == {
        "effort": "medium",
        "summary": "auto",
    }
