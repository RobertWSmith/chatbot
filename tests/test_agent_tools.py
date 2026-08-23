from app.models import ChatThread, User
from app.services.agent import _build_agent_tools, _system_prompt


def test_agent_registers_only_memory_tools_locally(app):
    """Ensure the chatbot's local tools are limited to memory operations."""
    with app.app_context():
        user = User(id=1, email="search@example.com")
        thread = ChatThread(id="thread-1", user_id=user.id)
        tools = _build_agent_tools(user, thread, {"memory_enabled": True, "privacy": {}})

    assert {tool.name for tool in tools} == {
        "recall_user_memory",
        "propose_memory",
    }


def test_system_prompt_routes_external_data_exclusively_through_mcp():
    """Ensure system instructions reserve external data access for MCP tools."""
    prompt = _system_prompt({}, ("portal",))

    assert "Use MCP tools for all external data and actions" in prompt
    assert "no built-in web search or URL-fetching fallback" in prompt


def test_system_prompt_discloses_when_no_mcp_external_tools_are_loaded():
    """Ensure the agent does not imply an unavailable external-data fallback."""
    prompt = _system_prompt({})

    assert "No external-data tools are available for this request" in prompt
