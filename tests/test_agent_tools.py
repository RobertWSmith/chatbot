from threading import Barrier, Lock
from time import sleep

import pytest
from langchain.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.models import ChatThread, User
from app.services import agent as agent_service
from app.services.agent import (
    _agent_run_config,
    _build_agent_tools,
    _build_web_search_api,
    _run_web_search,
    _system_prompt,
)


def _run_tool_calls(tools, tool_calls, max_concurrency=2):
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    result = graph.invoke(
        {"messages": [AIMessage(content="", tool_calls=tool_calls)]},
        config={"max_concurrency": max_concurrency},
    )
    return result["messages"][1:]


def test_agent_registers_duckduckgo_web_search_tool(app):
    with app.app_context():
        user = User(id=1, email="search@example.com")
        thread = ChatThread(id="thread-1", user_id=user.id)
        tools = _build_agent_tools(
            user, thread, {"memory_enabled": True, "privacy": {}}
        )

    assert {tool.name for tool in tools} == {
        "recall_user_memory",
        "propose_memory",
        "web_search",
        "resolve_web_link",
    }
    web_search = next(tool for tool in tools if tool.name == "web_search")
    assert "DuckDuckGo" in web_search.description
    assert "current" in web_search.description
    resolver = next(tool for tool in tools if tool.name == "resolve_web_link")
    assert "URL" in resolver.description
    assert "question" in resolver.description


def test_web_search_uses_duckduckgo_backend_with_valid_region():
    search_api = _build_web_search_api()

    assert search_api.backend == "duckduckgo"
    assert search_api.region == "us-en"
    assert search_api.time is None


def test_web_search_returns_source_urls():
    class SearchAPI:
        def results(self, query, max_results, source):
            assert query == "Agents SDK tracing"
            assert max_results == 5
            assert source == "text"
            return [
                {
                    "title": "Agents SDK",
                    "link": "https://platform.openai.com/docs/guides/agents-sdk",
                    "snippet": "Build agents with tools, handoffs, and tracing.",
                }
            ]

    result = _run_web_search(SearchAPI(), "Agents SDK tracing")

    assert "Title: Agents SDK" in result
    assert "URL: https://platform.openai.com/docs/guides/agents-sdk" in result
    assert "Snippet: Build agents with tools" in result


def test_web_search_failure_is_returned_as_tool_result():
    class FailingSearchAPI:
        def results(self, query, max_results, source):
            raise TimeoutError("operation timed out")

    result = _run_web_search(FailingSearchAPI(), "Agents SDK tracing")

    assert (
        result
        == "Web search is temporarily unavailable. Ask the user to retry shortly."
    )


def test_system_prompt_explains_web_search_policy():
    prompt = _system_prompt({})

    assert "Use web_search for current events" in prompt
    assert "Use resolve_web_link after web_search" in prompt
    assert "include source links" in prompt
    assert "issue them together in the same response" in prompt
    assert "wait for web_search to return URLs" in prompt
    assert "Call propose_memory one at a time" in prompt


def test_system_prompt_prefers_portal_search_when_authorized():
    prompt = _system_prompt({}, ("portal",))

    assert "Remote MCP tools are available" in prompt
    assert "Prefer the MCP Portal DuckDuckGo search tool" in prompt


def test_system_prompt_starts_with_the_users_custom_prompt():
    custom_prompt = "You are an encouraging science tutor."

    prompt = _system_prompt({"system_prompt": custom_prompt})

    assert prompt.startswith(custom_prompt)
    assert "Application capabilities and requirements:" in prompt
    assert "Use web_search for current events" in prompt


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (0, 1),
        (3, 3),
        (100, 16),
        ("invalid", 4),
    ],
)
def test_agent_run_config_bounds_tool_concurrency(app, configured, expected):
    with app.app_context():
        app.config["TOOL_MAX_CONCURRENCY"] = configured
        config = _agent_run_config("thread-1")

    assert config == {
        "configurable": {"thread_id": "thread-1"},
        "max_concurrency": expected,
    }


def test_independent_web_resolutions_run_concurrently(app, monkeypatch):
    rendezvous = Barrier(2)

    def fake_resolver(url, question):
        rendezvous.wait(timeout=2)
        return f"{url}: {question}"

    monkeypatch.setattr(agent_service, "fetch_web_link", fake_resolver)
    with app.app_context():
        user = User(id=1, email="parallel-web@example.com")
        thread = ChatThread(id="thread-1", user_id=user.id)
        tools = _build_agent_tools(
            user, thread, {"memory_enabled": True, "privacy": {}}
        )
        messages = _run_tool_calls(
            tools,
            [
                {
                    "name": "resolve_web_link",
                    "args": {"url": "https://one.example", "question": "first"},
                    "id": "call-1",
                    "type": "tool_call",
                },
                {
                    "name": "resolve_web_link",
                    "args": {"url": "https://two.example", "question": "second"},
                    "id": "call-2",
                    "type": "tool_call",
                },
            ],
        )

    assert [message.tool_call_id for message in messages] == ["call-1", "call-2"]
    assert [message.content for message in messages] == [
        "https://one.example: first",
        "https://two.example: second",
    ]


def test_memory_tools_serialize_shared_database_session(app, monkeypatch):
    activity_lock = Lock()
    activity = {"current": 0, "maximum": 0}

    def fake_search(_user_id, _query, limit):
        assert limit == 5
        with activity_lock:
            activity["current"] += 1
            activity["maximum"] = max(activity["maximum"], activity["current"])
        sleep(0.05)
        with activity_lock:
            activity["current"] -= 1
        return []

    monkeypatch.setattr(agent_service, "search_long_term_memories", fake_search)
    with app.app_context():
        user = User(id=1, email="serial-memory@example.com")
        thread = ChatThread(id="thread-1", user_id=user.id)
        tools = _build_agent_tools(
            user, thread, {"memory_enabled": True, "privacy": {}}
        )
        _run_tool_calls(
            tools,
            [
                {
                    "name": "recall_user_memory",
                    "args": {"query": "first"},
                    "id": "call-1",
                    "type": "tool_call",
                },
                {
                    "name": "recall_user_memory",
                    "args": {"query": "second"},
                    "id": "call-2",
                    "type": "tool_call",
                },
            ],
        )

    assert activity["maximum"] == 1
