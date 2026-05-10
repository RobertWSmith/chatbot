from __future__ import annotations

import re
from collections.abc import Generator
from typing import Any

from flask import current_app

from app.extensions import db
from app.memory.store import open_memory_store, user_memory_namespace
from app.models import ChatThread, PendingMemory, User


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
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def recall_user_memory(query: str) -> str:
        """Search approved long-term memories for the current user."""
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        with open_memory_store() as store:
            if store is None:
                return "Memory store is not configured."
            items = store.search(user_memory_namespace(user.id), query=query, limit=5)
        if not items:
            return "No relevant memories found."
        return "\n".join(item.value.get("text", "") for item in items)

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
        tools=[recall_user_memory, propose_memory],
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
        "Use recall_user_memory only when user-specific remembered context would help. "
        "Use propose_memory only for stable user preferences or facts worth remembering, "
        "and understand that the user must approve every proposed memory before it persists. "
        f"User settings: compact_mode={settings.get('compact_mode')}, "
        f"font_size={settings.get('font_size')}."
    )
