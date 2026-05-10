from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

MAX_DOWNLOAD_BYTES = 750_000
MAX_RESULT_CHARS = 6_000
MAX_REDIRECTS = 4
REQUEST_TIMEOUT = (3.05, 10)
USER_AGENT = "LangGraphChatbot/1.0 (+https://duckduckgo.com)"


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and attrs_dict.get("name", "").lower() == "description":
            self.description = attrs_dict.get("content", "").strip()
        elif tag in {"p", "br", "li", "div", "section", "article", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in {"p", "li", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = unescape(data).strip()
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        else:
            self._chunks.append(text)

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self._chunks))


def resolve_web_link(url: str, question: str = "") -> str:
    """Fetch a public web page and return readable content relevant to the question."""
    if not url:
        return "No URL was provided."
    try:
        final_url, content_type, body = fetch_public_url(url)
    except ValueError as exc:
        return f"Could not resolve URL: {exc}"
    except requests.RequestException as exc:
        return f"Could not fetch URL: {exc}"

    title = ""
    description = ""
    if "html" in content_type:
        parser = ReadableHTMLParser()
        parser.feed(body)
        title = normalize_text(parser.title)
        description = normalize_text(parser.description)
        text = parser.text
    else:
        text = normalize_text(body)

    excerpts = select_relevant_excerpts(text, question)
    if not excerpts:
        excerpts = text[:MAX_RESULT_CHARS]

    parts = [f"Source URL: {final_url}"]
    if title:
        parts.append(f"Title: {title}")
    if description:
        parts.append(f"Description: {description}")
    parts.append(f"Relevant content:\n{excerpts[:MAX_RESULT_CHARS]}")
    return "\n\n".join(parts)


def fetch_public_url(url: str) -> tuple[str, str, str]:
    current_url = normalize_url(url)
    session = requests.Session()
    for _ in range(MAX_REDIRECTS + 1):
        validate_public_url(current_url)
        response = session.get(
            current_url,
            headers={"User-Agent": USER_AGENT},
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("redirect response did not include a Location header")
            current_url = urljoin(current_url, location)
            continue
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "text/plain").split(";")[0].lower()
        if not is_supported_content_type(content_type):
            raise ValueError(f"unsupported content type {content_type!r}")
        body = read_limited_response(response)
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        return response.url, content_type, body.decode(encoding, errors="replace")
    raise ValueError("too many redirects")


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return f"https://{url.strip()}"
    return url.strip()


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs can be resolved")
    if not parsed.hostname:
        raise ValueError("URL is missing a hostname")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise ValueError("local hostnames cannot be resolved")
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"hostname could not be resolved: {hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("private or local network addresses cannot be resolved")


def is_supported_content_type(content_type: str) -> bool:
    return (
        content_type.startswith("text/")
        or content_type in {"application/json", "application/xml", "application/xhtml+xml"}
    )


def read_limited_response(response: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16_384):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise ValueError("response was too large to resolve safely")
        chunks.append(chunk)
    return b"".join(chunks)


def select_relevant_excerpts(text: str, question: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", text) if part.strip()]
    if not paragraphs:
        return ""
    terms = {
        term
        for term in re.findall(r"[a-zA-Z0-9]{3,}", question.lower())
        if term not in {"the", "and", "for", "with", "that", "this", "from", "what", "when", "where"}
    }
    if not terms:
        return "\n\n".join(paragraphs[:12])[:MAX_RESULT_CHARS]
    scored = []
    for index, paragraph in enumerate(paragraphs):
        words = set(re.findall(r"[a-zA-Z0-9]{3,}", paragraph.lower()))
        score = len(terms & words)
        if score:
            scored.append((score, -index, paragraph))
    selected = [paragraph for _score, _index, paragraph in sorted(scored, reverse=True)[:10]]
    if not selected:
        selected = paragraphs[:12]
    return "\n\n".join(selected)[:MAX_RESULT_CHARS]


def normalize_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()
