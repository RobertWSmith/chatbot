from __future__ import annotations

import re
from collections.abc import Generator
from threading import Lock
from typing import Any, TypedDict

from flask import current_app

from app.extensions import db
from app.models import ChatMessage, ChatThread, PendingMemory, User
from app.services.conversation_summary import (
    snapshot_context_message,
    summarize_overflowing_conversation,
)
from app.services.rag_memory import (
    format_memory_search_results,
    search_long_term_memories,
)
from app.services.web_resolver import resolve_web_link as fetch_web_link

DEFAULT_TOOL_MAX_CONCURRENCY = 4
MAX_TOOL_MAX_CONCURRENCY = 16


def stream_agent_response(
    user: User, thread: ChatThread, prompt: str
) -> Generator[dict, None, None]:
    settings = user.settings.merged()
    settings["reasoning_provider"] = (
        thread.reasoning_provider or settings["reasoning_provider"]
    )
    messages = _conversation_messages(user, thread, prompt)
    if not current_app.config.get("OPENAI_API_KEY"):
        yield from _stream_demo_response(user, thread, prompt, settings, messages)
        return

    try:
        if settings["reasoning_provider"] == "openai":
            yield from _stream_langgraph_response(user, thread, messages, settings)
        elif settings["reasoning_provider"] == "langgraph":
            yield from _stream_custom_reasoning_graph_response(
                user, thread, messages, settings
            )
        else:
            raise ValueError(
                f"Unsupported reasoning provider: {settings['reasoning_provider']}"
            )
    except ImportError as exc:
        yield {"type": "status", "text": "LangGraph dependencies are unavailable."}
        yield {"type": "token", "text": f"LangGraph is not installed: `{exc}`"}


def _stream_langgraph_response(
    user: User, thread: ChatThread, messages: list[dict[str, str]], settings: dict
) -> Generator[dict, None, None]:
    from langchain.agents import create_agent
    from langchain.messages import AIMessageChunk

    model = _build_chat_model(settings, provider_reasoning=True)
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
                if block["type"] == "reasoning" and settings.get(
                    "reasoning_summaries_enabled"
                ):
                    text = _reasoning_summary_text(block)
                    if text:
                        yield {"type": "reasoning_summary", "text": text}
                elif block["type"] == "text" and block.get("text"):
                    yield {"type": "token", "text": block["text"]}
        elif chunk["type"] == "updates":
            yield {"type": "status", "text": "Updated agent state"}


class ReasoningState(TypedDict, total=False):
    messages: list[dict[str, str]]
    context: str
    plan: str
    draft: str
    alternative_draft: str
    critique: str
    answer: str
    memory_proposal: dict[str, Any]


def _stream_custom_reasoning_graph_response(
    user: User,
    thread: ChatThread,
    messages: list[dict[str, str]],
    settings: dict,
) -> Generator[dict, None, None]:
    from langgraph.graph import END, START, StateGraph

    model = _build_chat_model(settings, provider_reasoning=False)
    graph = StateGraph(ReasoningState)
    nodes = _reasoning_workflow_for_effort(settings["reasoning_effort"])
    node_builders = _custom_reasoning_nodes(user, thread, settings, model)

    previous = START
    for node_name in nodes:
        graph.add_node(node_name, node_builders[node_name])
        graph.add_edge(previous, node_name)
        previous = node_name
    graph.add_edge(previous, END)

    compiled = graph.compile()
    yield {"type": "status", "text": "Running LangGraph reasoning workflow"}
    for update in compiled.stream(
        {"messages": messages},
        stream_mode="updates",
        config=_agent_run_config(thread.id),
    ):
        for node_name, values in update.items():
            yield {"type": "status", "text": _reasoning_status(node_name)}
            yield from _reasoning_events(values or {}, settings)


def _build_chat_model(settings: dict, *, provider_reasoning: bool):
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": settings["model_name"],
        "api_key": current_app.config.get("OPENAI_API_KEY") or None,
        "model_kwargs": {"parallel_tool_calls": True},
    }
    if provider_reasoning:
        kwargs["reasoning"] = {
            "effort": settings["reasoning_effort"],
            "summary": "auto" if settings.get("reasoning_summaries_enabled") else None,
        }
    return ChatOpenAI(**kwargs)


def _reasoning_workflow_for_effort(effort: str) -> list[str]:
    workflows = {
        "minimal": ["answer"],
        "low": ["gather_context", "answer"],
        "medium": ["gather_context", "plan", "answer"],
        "high": [
            "gather_context",
            "plan",
            "draft",
            "critique",
            "finalize",
            "maybe_propose_memory",
        ],
        "xhigh": [
            "gather_context",
            "plan",
            "draft",
            "alternative_draft",
            "critique",
            "finalize",
            "maybe_propose_memory",
        ],
    }
    return workflows.get(effort, workflows["medium"])


def _custom_reasoning_nodes(
    user: User,
    thread: ChatThread,
    settings: dict,
    model: Any,
) -> dict[str, Any]:
    def gather_context(state: ReasoningState) -> dict[str, str]:
        context_parts = [_format_recent_context(state["messages"])]
        if settings.get("memory_enabled", True):
            memory_context = _safe_memory_context(
                user, _last_user_message(state["messages"])
            )
            if memory_context:
                context_parts.append(memory_context)
        return {"context": "\n\n".join(part for part in context_parts if part)}

    def plan(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Create a short, user-safe answer plan. Do not reveal hidden "
                        "chain-of-thought; list only the public approach."
                    ),
                },
                {
                    "role": "user",
                    "content": _reasoning_prompt(state, "Plan the answer."),
                },
            ]
        )
        return {"plan": _message_text(response)}

    def answer(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {"role": "system", "content": _system_prompt(settings)},
                {
                    "role": "user",
                    "content": _reasoning_prompt(
                        state,
                        "Answer the user's latest message directly. Return only the final answer.",
                    ),
                },
            ]
        )
        return {"answer": _message_text(response)}

    def draft(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {"role": "system", "content": _system_prompt(settings)},
                {
                    "role": "user",
                    "content": _reasoning_prompt(
                        state,
                        "Write a strong draft answer. It can be improved later.",
                    ),
                },
            ]
        )
        return {"draft": _message_text(response)}

    def alternative_draft(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {"role": "system", "content": _system_prompt(settings)},
                {
                    "role": "user",
                    "content": _reasoning_prompt(
                        state,
                        "Write an alternate draft with a different organization or emphasis.",
                    ),
                },
            ]
        )
        return {"alternative_draft": _message_text(response)}

    def critique(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Critique the draft answer for correctness, missing caveats, "
                        "unsupported claims, and clarity. Keep it concise and user-safe."
                    ),
                },
                {
                    "role": "user",
                    "content": _reasoning_prompt(
                        state, "Review the draft before finalizing."
                    ),
                },
            ]
        )
        return {"critique": _message_text(response)}

    def finalize(state: ReasoningState) -> dict[str, str]:
        response = model.invoke(
            [
                {"role": "system", "content": _system_prompt(settings)},
                {
                    "role": "user",
                    "content": _reasoning_prompt(
                        state,
                        "Revise using the critique. Return only the final user-facing answer.",
                    ),
                },
            ]
        )
        return {"answer": _message_text(response)}

    def maybe_propose_memory(state: ReasoningState) -> dict[str, Any]:
        prompt = _last_user_message(state["messages"])
        if not _should_create_memory_proposal(prompt, settings):
            return {}
        proposal = PendingMemory(
            user_id=user.id,
            source_thread_id=thread.id,
            memory_text=prompt[:4000],
            category="user_request",
            confidence=0.6,
        )
        db.session.add(proposal)
        db.session.commit()
        return {
            "memory_proposal": {
                "id": proposal.id,
                "memory_text": proposal.memory_text,
                "category": proposal.category,
                "confidence": proposal.confidence,
            }
        }

    return {
        "gather_context": gather_context,
        "plan": plan,
        "answer": answer,
        "draft": draft,
        "alternative_draft": alternative_draft,
        "critique": critique,
        "finalize": finalize,
        "maybe_propose_memory": maybe_propose_memory,
    }


def _reasoning_events(
    values: dict[str, Any], settings: dict
) -> Generator[dict, None, None]:
    if settings.get("reasoning_summaries_enabled"):
        if plan := values.get("plan"):
            yield {"type": "reasoning_summary", "text": f"Plan:\n\n{plan}\n\n"}
        if critique := values.get("critique"):
            yield {
                "type": "reasoning_summary",
                "text": f"Self-check:\n\n{critique}\n\n",
            }
    if answer := values.get("answer"):
        yield {"type": "token", "text": answer}
    if memory_proposal := values.get("memory_proposal"):
        yield {"type": "memory_proposal", **memory_proposal}


def _reasoning_status(node_name: str) -> str:
    statuses = {
        "gather_context": "Gathered context",
        "plan": "Planned answer",
        "answer": "Generated answer",
        "draft": "Drafted answer",
        "alternative_draft": "Compared alternate draft",
        "critique": "Checked draft",
        "finalize": "Finalized answer",
        "maybe_propose_memory": "Checked memory proposals",
    }
    return statuses.get(node_name, "Updated reasoning graph")


def _reasoning_prompt(state: ReasoningState, instruction: str) -> str:
    parts = [instruction, f"Messages:\n{_format_messages(state['messages'])}"]
    if context := state.get("context"):
        parts.append(f"Context:\n{context}")
    if plan := state.get("plan"):
        parts.append(f"Plan:\n{plan}")
    if draft := state.get("draft"):
        parts.append(f"Draft:\n{draft}")
    if alternative := state.get("alternative_draft"):
        parts.append(f"Alternate draft:\n{alternative}")
    if critique := state.get("critique"):
        parts.append(f"Critique:\n{critique}")
    return "\n\n".join(parts)


def _format_recent_context(messages: list[dict[str, str]]) -> str:
    recent = messages[-6:]
    if not recent:
        return ""
    return "Recent conversation:\n" + _format_messages(recent)


def _format_messages(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"{message['role']}: {message['content']}" for message in messages)


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _safe_memory_context(user: User, query: str) -> str:
    try:
        memories = search_long_term_memories(user.id, query, limit=5)
    except Exception:  # noqa: BLE001 - optional memory recall must not block a response
        return ""
    formatted = format_memory_search_results(memories)
    return f"Approved long-term memory:\n{formatted}" if formatted else ""


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _reasoning_summary_text(block: dict[str, Any]) -> str:
    reasoning = block.get("reasoning")
    if isinstance(reasoning, str):
        return reasoning

    summary = block.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, dict):
        text = summary.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(summary, list):
        return "".join(
            item.get("text", "")
            for item in summary
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def _should_create_memory_proposal(prompt: str, settings: dict) -> bool:
    return (
        settings.get("memory_enabled", True)
        and settings.get("privacy", {}).get("allow_memory_proposals", True)
        and "remember" in prompt.lower()
    )


def _conversation_messages(
    user: User, thread: ChatThread, prompt: str
) -> list[dict[str, str]]:
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
    if (
        not messages
        or messages[-1]["role"] != "user"
        or messages[-1]["content"] != prompt
    ):
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
    def propose_memory(
        memory_text: str, category: str = "preference", confidence: float = 0.5
    ) -> str:
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
    yield {
        "type": "status",
        "text": "Demo mode: set OPENAI_API_KEY to use LangGraph with OpenAI.",
    }
    if settings.get("reasoning_summaries_enabled"):
        yield {
            "type": "reasoning_summary",
            "text": "No API key is configured, so this local demo response validates streaming only.",
        }
    previous_messages = messages[:-1]
    context_note = ""
    if previous_messages:
        recent = previous_messages[-4:]
        context_lines = [
            f"- {message['role']}: {message['content'][:240]}" for message in recent
        ]
        context_note = (
            "Recent Postgres conversation context:\n\n"
            + "\n".join(context_lines)
            + "\n\n"
        )
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
