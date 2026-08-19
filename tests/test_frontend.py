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
    )

    for asset in assets:
        response = client.get(f"/static/js/{asset}")
        assert response.status_code == 200
        assert response.mimetype == "text/javascript"
