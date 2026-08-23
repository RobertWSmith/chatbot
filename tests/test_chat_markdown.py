from app.services.agent import _system_prompt

from .conftest import register


def test_chat_page_loads_math_renderer(client):
    """Ensure the chat page loads the KaTeX Markdown integration."""
    register(client)
    page = client.get("/chat")

    assert page.status_code == 200
    assert b"katex@0.18.1/dist/katex.min.css" in page.data
    assert b"katex@0.18.1/dist/katex.min.js" in page.data
    assert b"markdown-it-texmath@1.0.0/texmath.min.js" in page.data


def test_system_prompt_requests_supported_math_delimiters():
    """Ensure model instructions match the delimiters rendered by the browser."""
    prompt = _system_prompt({})

    assert "inline math as `$...$`" in prompt
    assert r"display math as `\[...\]`" in prompt
