from __future__ import annotations

import re
from collections.abc import Generator
from typing import Any

from flask import current_app

from app.extensions import db
from app.models import ChatThread, PendingMemory, User
from app.services.rag_memory import format_memory_search_results, search_long_term_memories
from app.services.web_resolver import resolve_web_link as fetch_web_link


def stream_agent_response(user: User, thread: ChatThread, prompt: str) -> Generator[dict, None, None]:
    settings = user.settings.merged()
    if not current_app.config.get("OPENAI_API_KEY"):
        yield from _stream_demo_response(user, thread, prompt, settings)
        return

    try:
        yield from _stream_langgraph_response(user, thread, prompt, settings)
    except ImportError as exc:
        yield {"type": "status", "text": "LangGraph dependencies are unavailable."}
        yield {"type": "token", "text": f"LangGraph is not installed: `{exc}`"}


def _stream_langgraph_response(
    user: User, thread: ChatThread, prompt: str, settings: dict
) -> Generator[dict, None, None]:
    from langchain.agents import create_agent
    from langchain.messages import AIMessageChunk
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings["model_name"],
        api_key=current_app.config.get("OPENAI_API_KEY") or None,
        model_kwargs={
            "reasoning": {
                "effort": settings["reasoning_effort"],
                "summary": "auto" if settings.get("reasoning_summaries_enabled") else None,
            }
        },
    )
    agent = create_agent(
        model=model,
        tools=_build_agent_tools(user, thread, settings),
        store=None,
        system_prompt=_system_prompt(settings),
    )

    yield {"type": "status", "text": "Thinking"}
    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": prompt}]},
        stream_mode=["messages", "updates"],
        version="v2",
        config={"configurable": {"thread_id": thread.id}},
    ):
        if chunk["type"] == "messages":
            message_chunk, _metadata = chunk["data"]
            if not isinstance(message_chunk, AIMessageChunk):
                continue
            for block in message_chunk.content_blocks:
                if block["type"] == "reasoning" and settings.get("reasoning_summaries_enabled"):
                    text = block.get("reasoning") or block.get("summary") or ""
                    if text:
                        yield {"type": "reasoning_summary", "text": text}
                elif block["type"] == "text" and block.get("text"):
                    yield {"type": "token", "text": block["text"]}
        elif chunk["type"] == "updates":
            yield {"type": "status", "text": "Updated agent state"}


def _build_agent_tools(user: User, thread: ChatThread, settings: dict) -> list[Any]:
    from langchain.tools import tool
    from langchain_community.tools import DuckDuckGoSearchRun

    @tool
    def recall_user_memory(query: str) -> str:
        """Retrieve approved long-term memories with vector similarity search."""
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        return format_memory_search_results(search_long_term_memories(user.id, query, limit=5))

    @tool
    def propose_memory(memory_text: str, category: str = "preference", confidence: float = 0.5) -> str:
        """Create a user-reviewable memory proposal. The user must approve it before saving."""
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        if not settings.get("privacy", {}).get("allow_memory_proposals", True):
            return "The user has disabled memory proposals."
        proposal = PendingMemory(
            user_id=user.id,
            source_thread_id=thread.id,
            memory_text=memory_text[:4000],
            category=category[:80] or "preference",
            confidence=max(0.0, min(float(confidence), 1.0)),
        )
        db.session.add(proposal)
        db.session.commit()
        return f"Created memory proposal {proposal.id} for user review."

    @tool
    def resolve_web_link(url: str, question: str = "") -> str:
        """Fetch a public web result URL and extract readable content relevant to the question."""
        return fetch_web_link(url, question)

    web_search = DuckDuckGoSearchRun(
        name="web_search",
        description=(
            "Search DuckDuckGo for current or external web information. "
            "Use this when the user asks about recent events, facts that may have changed, "
            "or topics that require sources outside this chatbot's conversation history."
        ),
    )
    return [recall_user_memory, propose_memory, web_search, resolve_web_link]


def _stream_demo_response(
    user: User, thread: ChatThread, prompt: str, settings: dict
) -> Generator[dict, None, None]:
    yield {"type": "status", "text": "Demo mode: set OPENAI_API_KEY to use LangGraph with OpenAI."}
    if settings.get("reasoning_summaries_enabled"):
        yield {
            "type": "reasoning_summary",
            "text": "No API key is configured, so this local demo response validates streaming only.",
        }
    text = (
        "I am running in **local demo mode** because no OpenAI API key is configured.\n\n"
        "Your message was:\n\n"
        f"> {prompt}\n\n"
        "Once `OPENAI_API_KEY` and Postgres are configured, this endpoint streams LangGraph "
        "messages, tool progress, reasoning summaries, and user-approved memory proposals."
    )
    for token in re.split(r"(\s+)", text):
        if token:
            yield {"type": "token", "text": token}
    if settings.get("memory_enabled") and "remember" in prompt.lower():
        proposal = PendingMemory(
            user_id=user.id,
            source_thread_id=thread.id,
            memory_text=prompt[:4000],
            category="user_request",
            confidence=0.6,
        )
        db.session.add(proposal)
        db.session.commit()
        yield {
            "type": "memory_proposal",
            "id": proposal.id,
            "memory_text": proposal.memory_text,
            "category": proposal.category,
            "confidence": proposal.confidence,
        }


def _system_prompt(settings: dict[str, Any]) -> str:
    return (
        "You are a helpful chatbot. Respond in clean GitHub-flavored Markdown. "
        "Use web_search for current events, recently changed facts, or external information "
        "that is not available from the conversation. Summarize search results plainly and "
        "include source links when the tool returns them. Use resolve_web_link after web_search "
        "when a search result needs to be opened to answer the user's specific question. "
        "Use recall_user_memory as a RAG retriever over approved long-term memories "
        "when user-specific remembered context would help. "
        "Use propose_memory only for stable user preferences or facts worth remembering, "
        "and understand that the user must approve every proposed memory before it persists. "
        f"User settings: compact_mode={settings.get('compact_mode')}, "
        f"font_size={settings.get('font_size')}."
    )
