from types import SimpleNamespace

from app.extensions import db
from app.models import ChatThread, PendingMemory, User
from app.services import agent as agent_service

from .conftest import register


class FakeChatModel:
    def invoke(self, messages):
        prompt = messages[-1]["content"]
        if "Plan the answer" in prompt:
            return SimpleNamespace(content="Use the available context, then answer directly.")
        if "Write a strong draft answer" in prompt:
            return SimpleNamespace(content="Draft answer.")
        if "Review the draft" in prompt:
            return SimpleNamespace(content="The draft is clear and supported.")
        if "Revise using the critique" in prompt:
            return SimpleNamespace(content="Final answer from the custom graph.")
        return SimpleNamespace(content="Direct answer from the custom graph.")


def test_custom_reasoning_graph_streams_plan_and_answer(client, app, monkeypatch):
    app.config.update(OPENAI_API_KEY="test-key", CUSTOM_REASONING_GRAPH_ENABLED=True)
    register(client)

    monkeypatch.setattr(
        agent_service, "_build_chat_model", lambda settings, **kwargs: FakeChatModel()
    )
    monkeypatch.setattr(agent_service, "_safe_memory_context", lambda user, query: "")

    with app.app_context():
        user = User.query.one()
        thread = ChatThread(user_id=user.id)
        db.session.add(thread)
        db.session.commit()

        events = list(agent_service.stream_agent_response(user, thread, "How does this work?"))

    assert {"type": "status", "text": "Running custom reasoning graph"} in events
    assert any(
        event["type"] == "reasoning_summary" and "Plan:" in event["text"] for event in events
    )
    assert any(
        event == {"type": "token", "text": "Direct answer from the custom graph."}
        for event in events
    )


def test_high_effort_custom_graph_can_create_memory_proposal(client, app, monkeypatch):
    app.config.update(OPENAI_API_KEY="test-key", CUSTOM_REASONING_GRAPH_ENABLED=True)
    register(client)

    monkeypatch.setattr(
        agent_service, "_build_chat_model", lambda settings, **kwargs: FakeChatModel()
    )
    monkeypatch.setattr(agent_service, "_safe_memory_context", lambda user, query: "")

    with app.app_context():
        user = User.query.one()
        user.settings.data = {
            **user.settings.merged(),
            "reasoning_effort": "high",
        }
        thread = ChatThread(user_id=user.id)
        db.session.add(thread)
        db.session.commit()

        events = list(
            agent_service.stream_agent_response(user, thread, "Please remember I like graph flows.")
        )

        assert PendingMemory.query.filter_by(user_id=user.id, status="pending").count() == 1

    assert any(
        event["type"] == "reasoning_summary" and "Self-check:" in event["text"]
        for event in events
    )
    assert any(event["type"] == "memory_proposal" for event in events)
    assert any(
        event == {"type": "token", "text": "Final answer from the custom graph."}
        for event in events
    )


def test_high_effort_custom_graph_handles_no_memory_proposal(client, app, monkeypatch):
    app.config.update(OPENAI_API_KEY="test-key", CUSTOM_REASONING_GRAPH_ENABLED=True)
    register(client)

    monkeypatch.setattr(
        agent_service, "_build_chat_model", lambda settings, **kwargs: FakeChatModel()
    )
    monkeypatch.setattr(agent_service, "_safe_memory_context", lambda user, query: "")

    with app.app_context():
        user = User.query.one()
        user.settings.data = {
            **user.settings.merged(),
            "reasoning_effort": "high",
        }
        thread = ChatThread(user_id=user.id)
        db.session.add(thread)
        db.session.commit()

        events = list(agent_service.stream_agent_response(user, thread, "Explain the graph."))

        assert PendingMemory.query.filter_by(user_id=user.id, status="pending").count() == 0

    assert not any(event["type"] == "memory_proposal" for event in events)
    assert any(
        event == {"type": "token", "text": "Final answer from the custom graph."}
        for event in events
    )


def test_reasoning_effort_selects_graph_workflow():
    assert agent_service._reasoning_workflow_for_effort("minimal") == ["answer"]
    assert agent_service._reasoning_workflow_for_effort("medium") == [
        "gather_context",
        "plan",
        "answer",
    ]
    assert "alternative_draft" in agent_service._reasoning_workflow_for_effort("xhigh")
