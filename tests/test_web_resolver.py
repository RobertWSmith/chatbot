import pytest

from app.services import web_resolver


def test_resolve_web_link_extracts_relevant_html(monkeypatch):
    """Ensure HTML metadata and question-relevant text are extracted."""
    html = """
    <html>
      <head>
        <title>Example release notes</title>
        <meta name="description" content="A short release summary">
      </head>
      <body>
        <nav>Navigation that should not matter</nav>
        <main>
          <h1>Release notes</h1>
          <p>The chatbot added DuckDuckGo web search.</p>
          <p>The resolver opens result links and extracts information relevant to the request.</p>
          <p>Unrelated billing copy.</p>
        </main>
      </body>
    </html>
    """

    monkeypatch.setattr(
        web_resolver,
        "fetch_public_url",
        lambda url: ("https://example.com/release", "text/html", html),
    )

    result = web_resolver.resolve_web_link(
        "https://example.com/release",
        "What does the resolver do?",
    )

    assert "Source URL: https://example.com/release" in result
    assert "Title: Example release notes" in result
    assert "Description: A short release summary" in result
    assert "resolver opens result links" in result


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ],
)
def test_validate_public_url_blocks_local_targets(url):
    """Ensure the resolver rejects non-public URL targets."""
    with pytest.raises(ValueError):
        web_resolver.validate_public_url(url)
