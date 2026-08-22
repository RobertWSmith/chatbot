from __future__ import annotations

import re
from collections.abc import Generator
from threading import Lock
from typing import Any

from flask import current_app

from app.extensions import db
from app.models import ChatMessage, ChatThread, PendingMemory, User
from app.services.conversation_summary import (
    snapshot_context_message,
    summarize_overflowing_conversation,
)
from app.services.rag_memory import format_memory_search_results, search_long_term_memories
from app.services.web_resolver import resolve_web_link as fetch_web_link

DEFAULT_TOOL_MAX_CONCURRENCY = 4
MAX_TOOL_MAX_CONCURRENCY = 16


def stream_agent_response(user: User, thread: ChatThread, prompt: str) -> Generator[dict, None, None]:
    settings = user.settings.merged()
    messages = _conversation_messages(user, thread, prompt)
    if not current_app.config.get("OPENAI_API_KEY"):
        yield from _stream_demo_response(user, thread, prompt, settings, messages)
        return

    try:
        yield from _stream_langgraph_response(user, thread, messages, settings)
    except ImportError as exc:
        yield {"type": "status", "text": "LangGraph dependencies are unavailable."}
        yield {"type": "token", "text": f"LangGraph is not installed: `{exc}`"}


def _stream_langgraph_response(
    user: User, thread: ChatThread, messages: list[dict[str, str]], settings: dict
) -> Generator[dict, None, None]:
    from langchain.agents import create_agent
    from langchain.messages import AIMessageChunk
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings["model_name"],
        api_key=current_app.config.get("OPENAI_API_KEY") or None,
        model_kwargs={
            "parallel_tool_calls": True,
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
        {"messages": messages},
        stream_mode=["messages", "updates"],
        version="v2",
        config=_agent_run_config(thread.id),
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


def _conversation_messages(user: User, thread: ChatThread, prompt: str) -> list[dict[str, str]]:
    limit = max(1, int(current_app.config.get("CONVERSATION_HISTORY_LIMIT", 24)))
    rows = (
        ChatMessage.query.filter(
            ChatMessage.thread_id == thread.id,
            ChatMessage.user_id == user.id,
            ChatMessage.role.in_(("user", "assistant")),
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .all()
    )
    rows.reverse()
    snapshot = summarize_overflowing_conversation(user, thread, rows, keep_count=limit)
    recent_rows = rows[-limit:]
    messages = [
        {"role": row.role, "content": row.content}
        for row in recent_rows
        if row.content and row.role in {"user", "assistant"}
    ]
    context_message = snapshot_context_message(snapshot)
    if context_message:
        messages.insert(0, context_message)
    if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != prompt:
        messages.append({"role": "user", "content": prompt})
    return messages


def _build_agent_tools(user: User, thread: ChatThread, settings: dict) -> list[Any]:
    from langchain.tools import tool
    from langchain_community.tools import DuckDuckGoSearchRun

    # LangGraph runs tool calls from the same model turn concurrently. Flask-SQLAlchemy's
    # scoped session must not be used by multiple worker threads at once, so memory tools
    # share a per-agent lock while network-only tools remain concurrent.
    memory_tool_lock = Lock()

    @tool
    def recall_user_memory(query: str) -> str:
        """Retrieve approved long-term memories with vector similarity search."""
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        with memory_tool_lock:
            results = search_long_term_memories(user.id, query, limit=5)
            return format_memory_search_results(results)

    @tool
    def propose_memory(memory_text: str, category: str = "preference", confidence: float = 0.5) -> str:
        """Create a user-reviewable memory proposal. The user must approve it before saving."""
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        if not settings.get("privacy", {}).get("allow_memory_proposals", True):
            return "The user has disabled memory proposals."
        with memory_tool_lock:
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
        """Fetch a known public result URL and extract content relevant to a question.

        Independent known URLs may be fetched together.
        """
        return fetch_web_link(url, question)

    web_search = DuckDuckGoSearchRun(
        name="web_search",
        description=(
            "Search DuckDuckGo for current or external web information. "
            "Use this when the user asks about recent events, facts that may have changed, "
            "or topics that require sources outside this chatbot's conversation history. "
            "Independent queries may be searched together in the same model turn."
        ),
    )
    return [recall_user_memory, propose_memory, web_search, resolve_web_link]


def _agent_run_config(thread_id: str) -> dict[str, Any]:
    configured = current_app.config.get(
        "TOOL_MAX_CONCURRENCY",
        DEFAULT_TOOL_MAX_CONCURRENCY,
    )
    try:
        max_concurrency = int(configured)
    except (TypeError, ValueError):
        max_concurrency = DEFAULT_TOOL_MAX_CONCURRENCY
    max_concurrency = max(1, min(max_concurrency, MAX_TOOL_MAX_CONCURRENCY))
    return {
        "configurable": {"thread_id": thread_id},
        "max_concurrency": max_concurrency,
    }


def _stream_demo_response(
    user: User,
    thread: ChatThread,
    prompt: str,
    settings: dict,
    messages: list[dict[str, str]],
) -> Generator[dict, None, None]:
    yield {"type": "status", "text": "Demo mode: set OPENAI_API_KEY to use LangGraph with OpenAI."}
    if settings.get("reasoning_summaries_enabled"):
        yield {
            "type": "reasoning_summary",
            "text": "No API key is configured, so this local demo response validates streaming only.",
        }
    previous_messages = messages[:-1]
    context_note = ""
    if previous_messages:
        recent = previous_messages[-4:]
        context_lines = [f"- {message['role']}: {message['content'][:240]}" for message in recent]
        context_note = "Recent Postgres conversation context:\n\n" + "\n".join(context_lines) + "\n\n"
    text = (
        "I am running in **local demo mode** because no OpenAI API key is configured.\n\n"
        f"{context_note}"
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
        "When two or more tool calls are independent and all of their arguments are already "
        "known, issue them together in the same response so they can run concurrently. Do not "
        "guess arguments or parallelize across a dependency: wait for web_search to return URLs "
        "before calling resolve_web_link. Call propose_memory one at a time and never include it "
        "in a parallel tool batch. "
        "Use recall_user_memory as a RAG retriever over approved long-term memories "
        "when user-specific remembered context would help. "
        "Use the rolling conversation summary plus prior messages in this thread as "
        "short-term conversation memory; if the user refers to something earlier in the "
        "same chat, answer from that context. "
        "Use propose_memory only for stable user preferences or facts worth remembering, "
        "and understand that the user must approve every proposed memory before it persists. "
        f"User settings: compact_mode={settings.get('compact_mode')}, "
        f"font_size={settings.get('font_size')}."
    )
