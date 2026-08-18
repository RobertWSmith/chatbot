from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.messages import AIMessageChunk

from app.extensions import db
from app.models import ChatThread, PendingMemory, User
from app.services import agent as agent_service

from .conftest import register


class FakeChatModel:
    """Provide deterministic responses for custom graph tests."""

    def invoke(self, messages):
        """Return a response selected from the latest node instruction."""
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


class ResearchRoutingChatModel(FakeChatModel):
    """Request MCP research at each eligible routing decision."""

    def __init__(self):
        """Initialize captured prompts for tool-isolation assertions."""
        self.prompts = []

    def invoke(self, messages):
        """Return research decisions while preserving standard node responses."""
        self.prompts.append(messages)
        system_prompt = messages[0]["content"]
        if system_prompt.startswith("Make a routing decision only"):
            return SimpleNamespace(
                content="RESEARCH_NEEDED: yes\nRESEARCH_QUERY: Find current source data."
            )
        if system_prompt.startswith("Review evidence sufficiency only"):
            return SimpleNamespace(
                content="RESEARCH_NEEDED: yes\nRESEARCH_QUERY: Verify the missing evidence."
            )
        return super().invoke(messages)


class FakeMCPResearchAgent:
    """Record isolated MCP-node invocations and return deterministic notes."""

    def __init__(self):
        """Initialize the captured nested-agent calls."""
        self.calls = []

    async def ainvoke(self, payload, config=None):
        """Return one research note for each nested-agent invocation."""
        self.calls.append((payload, config))
        call_number = len(self.calls)
        return {
            "messages": [SimpleNamespace(content=f"External research from MCP pass {call_number}.")]
        }


class FakePrebuiltAgent:
    """Provide a deterministic provider-reasoning stream for tests."""

    def stream(self, *args, **kwargs):
        """Yield one Responses API-style reasoning summary block."""
        yield {
            "type": "messages",
            "data": (
                AIMessageChunk(
                    content=[
                        {
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": "Prepared the response."}],
                        }
                    ]
                ),
                {},
            ),
        }


def test_custom_reasoning_graph_streams_plan_and_answer(client, app, monkeypatch):
    """Ensure medium effort streams a public plan and final answer."""
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
    """Ensure high effort can create a reviewable memory proposal."""
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
        event["type"] == "reasoning_summary" and "Self-check:" in event["text"] for event in events
    )
    assert any(event["type"] == "memory_proposal" for event in events)
    assert any(
        event == {"type": "token", "text": "Final answer from the custom graph."}
        for event in events
    )


def test_high_effort_custom_graph_handles_no_memory_proposal(client, app, monkeypatch):
    """Ensure high effort completes when no memory proposal is warranted."""
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


def test_mcp_enabled_custom_graph_scopes_read_tools_and_bounds_research(client, app, monkeypatch):
    """Ensure only the research node gets read-only MCP tools for two bounded passes."""
    app.config.update(
        OPENAI_API_KEY="test-key",
        CUSTOM_REASONING_GRAPH_ENABLED=True,
        CUSTOM_REASONING_MAX_RESEARCH_ROUNDS=2,
    )
    register(client)
    model = ResearchRoutingChatModel()
    research_agent = FakeMCPResearchAgent()
    read_tool = SimpleNamespace(
        name="mcp_portal_search",
        description="Search current public information.",
        metadata={"readOnlyHint": True, "destructiveHint": False},
    )
    write_tool = SimpleNamespace(
        name="mcp_portal_delete",
        description="Delete an external record.",
        metadata={"readOnlyHint": False, "destructiveHint": True},
    )
    built = {}

    def build_research_agent(model_arg, tools, namespaces):
        """Capture the tools passed exclusively to the nested research agent."""
        built.update(model=model_arg, tools=tools, namespaces=namespaces)
        return research_agent

    monkeypatch.setattr(agent_service, "_build_chat_model", lambda settings, **kwargs: model)
    monkeypatch.setattr(agent_service, "_safe_memory_context", lambda user, query: "")
    monkeypatch.setattr(
        agent_service,
        "accessible_mcp_namespaces",
        lambda user_id: [SimpleNamespace(namespace="portalv2")],
    )
    monkeypatch.setattr(
        agent_service,
        "load_authorized_mcp_tools",
        AsyncMock(
            return_value=agent_service.MCPToolLoadResult(
                [read_tool, write_tool],
                ("portalv2",),
                (),
            )
        ),
    )
    monkeypatch.setattr(agent_service, "_build_mcp_research_agent", build_research_agent)

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
            agent_service.stream_agent_response(
                user,
                thread,
                "What does the current external source say?",
            )
        )

    assert {"type": "status", "text": "Running custom reasoning graph"} in events
    assert sum(event.get("text") == "Gathered MCP research" for event in events) == 2
    assert len(research_agent.calls) == 2
    assert built["tools"] == [read_tool]
    assert write_tool not in built["tools"]
    assert built["namespaces"] == ("portalv2",)
    assert any("External research notes:" in messages[-1]["content"] for messages in model.prompts)
    assert any(
        event == {"type": "token", "text": "Final answer from the custom graph."}
        for event in events
    )


def test_reasoning_effort_selects_graph_workflow():
    """Ensure each effort level maps to the intended graph topology."""
    assert agent_service._reasoning_workflow_for_effort("minimal") == ["answer"]
    assert agent_service._reasoning_workflow_for_effort("medium") == [
        "gather_context",
        "plan",
        "answer",
    ]
    assert "alternative_draft" in agent_service._reasoning_workflow_for_effort("xhigh")


def test_reasoning_summary_text_flattens_responses_api_blocks():
    """Ensure current Responses API summary blocks flatten to text."""
    block = {
        "type": "reasoning",
        "summary": [
            {"type": "summary_text", "text": "Checked the available context. "},
            {"type": "summary_text", "text": "Prepared the response."},
        ],
    }

    assert agent_service._reasoning_summary_text(block) == (
        "Checked the available context. Prepared the response."
    )


def test_reasoning_summary_text_supports_legacy_string_blocks():
    """Ensure legacy string reasoning blocks remain supported."""
    block = {"type": "reasoning", "reasoning": "Prepared the response."}

    assert agent_service._reasoning_summary_text(block) == "Prepared the response."


@pytest.mark.parametrize("effort", ["high", "xhigh"])
def test_prebuilt_agent_streams_reasoning_summary_text(monkeypatch, effort):
    """Ensure provider-native reasoning summaries become stream events."""
    monkeypatch.setattr("langchain.agents.create_agent", lambda **kwargs: FakePrebuiltAgent())
    monkeypatch.setattr(agent_service, "_build_chat_model", lambda settings, **kwargs: object())
    monkeypatch.setattr(agent_service, "_build_agent_tools", lambda user, thread, settings: [])

    events = list(
        agent_service._stream_langgraph_response(
            SimpleNamespace(),
            SimpleNamespace(id=1),
            [],
            {
                "reasoning_effort": effort,
                "reasoning_summaries_enabled": True,
            },
        )
    )

    assert {"type": "reasoning_summary", "text": "Prepared the response."} in events
