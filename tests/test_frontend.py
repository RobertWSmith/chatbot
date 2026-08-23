import re

from .conftest import register


def test_interactive_pages_load_module_entrypoints_without_htmx(client):
    """Ensure interactive pages use native modules without the removed HTMX dependency."""
    register(client)
    pages = {
        "/chat": "chat.js",
        "/memory": "memory.js",
        "/settings": "settings.js",
    }

    for path, entrypoint in pages.items():
        response = client.get(path)
        expected = f'<script type="module" src="/static/js/{entrypoint}"></script>'.encode()
        assert response.status_code == 200
        assert expected in response.data
        assert b"htmx" not in response.data


def test_browser_module_dependencies_are_served(client):
    """Ensure every shared browser module is available from Flask static assets."""
    assets = (
        "admin-mcp-view.js",
        "app.js",
        "chat-rendering.js",
        "chat-stream.js",
        "chat-tools.js",
    )

    for asset in assets:
        response = client.get(f"/static/js/{asset}")
        assert response.status_code == 200
        assert response.mimetype == "text/javascript"


def test_markdown_links_open_in_new_tabs(client):
    """Ensure rendered chat links preserve the current conversation tab."""
    response = client.get("/static/js/chat-rendering.js")

    assert response.status_code == 200
    assert b'link.setAttribute("target", "_blank")' in response.data
    assert b'link.setAttribute("rel", "noopener noreferrer")' in response.data


def test_tool_filter_can_escape_the_composer_surface(client):
    """Ensure the upward-opening tool filter is not clipped by its composer ancestor."""
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    composer_surface = re.search(rb"\.composer-surface\s*\{([^}]*)\}", response.data)
    assert composer_surface is not None
    assert b"overflow: visible" in composer_surface.group(1)


def test_settings_page_exposes_gpt_5_6_options(client):
    """Ensure the settings selectors expose GPT-5.6 models and reasoning efforts."""
    register(client)

    response = client.get("/settings")

    assert response.status_code == 200
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert f'value="{model}"'.encode() in response.data
    for effort in ("none", "low", "medium", "high", "xhigh", "max"):
        assert f'value="{effort}"'.encode() in response.data
    assert b'value="minimal"' not in response.data
