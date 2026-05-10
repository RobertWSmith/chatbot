from app.models import ChatThread, User
from app.services.agent import _build_agent_tools, _system_prompt


def test_agent_registers_duckduckgo_web_search_tool(app):
    with app.app_context():
        user = User(id=1, email="search@example.com")
        thread = ChatThread(id="thread-1", user_id=user.id)
        tools = _build_agent_tools(user, thread, {"memory_enabled": True, "privacy": {}})

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


def test_system_prompt_explains_web_search_policy():
    prompt = _system_prompt({})

    assert "Use web_search for current events" in prompt
    assert "Use resolve_web_link after web_search" in prompt
    assert "include source links" in prompt
