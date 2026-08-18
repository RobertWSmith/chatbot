from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Generator, Iterable
from typing import Any, TypedDict

from flask import current_app, has_app_context

from app.extensions import db
from app.models import ChatMessage, ChatThread, PendingMemory, User
from app.services.conversation_summary import (
    snapshot_context_message,
    summarize_overflowing_conversation,
)
from app.services.mcp_access import (
    MCPToolLoadResult,
    accessible_mcp_namespaces,
    load_authorized_mcp_tools,
)
from app.services.rag_memory import format_memory_search_results, search_long_term_memories

logger = logging.getLogger(__name__)


def stream_agent_response(
    user: User, thread: ChatThread, prompt: str
) -> Generator[dict, None, None]:
    """Stream response events for a user prompt.

    Args:
        user: Authenticated user requesting a response.
        thread: Chat thread containing the prompt.
        prompt: Latest user message.

    Yields:
        Status, reasoning-summary, token, and memory-proposal events.
    """
    settings = user.settings.merged()
    messages = _conversation_messages(user, thread, prompt)
    if not current_app.config.get("OPENAI_API_KEY"):
        logger.info(
            "event=agent.mode user_id=%s thread_id=%s mode=demo "
            "tool_capable=false reason=missing_openai_api_key",
            user.id,
            thread.id,
        )
        yield from _stream_demo_response(user, thread, prompt, settings, messages)
        return

    try:
        authorized_namespaces = accessible_mcp_namespaces(user.id)
        use_custom_graph = (
            current_app.config.get("CUSTOM_REASONING_GRAPH_ENABLED") and not authorized_namespaces
        )
        logger.info(
            "event=agent.mode user_id=%s thread_id=%s mode=%s tool_capable=%s "
            "authorized_mcp_namespaces=%s",
            user.id,
            thread.id,
            "custom_reasoning_graph" if use_custom_graph else "prebuilt_agent",
            str(not use_custom_graph).lower(),
            ",".join(item.namespace for item in authorized_namespaces) or "none",
        )
        if use_custom_graph:
            yield from _stream_custom_reasoning_graph_response(user, thread, messages, settings)
        else:
            yield from _stream_langgraph_response(user, thread, messages, settings)
    except ImportError as exc:
        yield {"type": "status", "text": "LangGraph dependencies are unavailable."}
        yield {"type": "token", "text": f"LangGraph is not installed: `{exc}`"}


def _stream_langgraph_response(
    user: User, thread: ChatThread, messages: list[dict[str, str]], settings: dict
) -> Generator[dict, None, None]:
    """Bridge the asynchronous LangGraph stream into synchronous Flask code.

    Args:
        user: Authenticated user requesting a response.
        thread: Active chat thread.
        messages: Model-ready conversation messages.
        settings: Effective user settings.

    Yields:
        Agent response events.
    """
    yield from _iterate_async_generator(
        _astream_langgraph_response(user, thread, messages, settings)
    )


async def _astream_langgraph_response(
    user: User, thread: ChatThread, messages: list[dict[str, str]], settings: dict
) -> AsyncGenerator[dict, None]:
    """Run the standard tool-capable LangGraph agent asynchronously.

    Args:
        user: Authenticated user requesting a response.
        thread: Active chat thread.
        messages: Model-ready conversation messages.
        settings: Effective user settings.

    Yields:
        Normalized response events from LangGraph stream chunks.
    """
    from langchain.agents import create_agent
    from langchain.messages import AIMessageChunk

    from app.services.tool_logging import ToolCallLoggingCallback

    model = _build_chat_model(settings, provider_reasoning=True)
    user_id = getattr(user, "id", None)
    mcp_result = (
        await load_authorized_mcp_tools(user_id)
        if user_id is not None
        else MCPToolLoadResult([], (), ())
    )
    for namespace in mcp_result.unavailable_namespaces:
        yield {"type": "status", "text": f"MCP namespace {namespace} is unavailable"}
    memory_tools = _build_agent_tools(user, thread, settings)
    agent_tools = [*memory_tools, *mcp_result.tools]
    logger.info(
        "event=agent.tool_inventory user_id=%s thread_id=%s memory_tools=%s "
        "mcp_namespaces=%s mcp_tools=%s unavailable_mcp_namespaces=%s",
        user_id,
        thread.id,
        ",".join(tool.name for tool in memory_tools) or "none",
        ",".join(mcp_result.loaded_namespaces) or "none",
        ",".join(tool.name for tool in mcp_result.tools) or "none",
        ",".join(mcp_result.unavailable_namespaces) or "none",
    )
    agent = create_agent(
        model=model,
        tools=agent_tools,
        store=None,
        system_prompt=_system_prompt(settings, mcp_result.loaded_namespaces),
    )
    tool_call_logger = ToolCallLoggingCallback(
        logger=logger,
        user_id=user_id,
        thread_id=str(thread.id),
        log_arguments=(
            bool(current_app.config.get("TOOL_CALL_LOG_ARGUMENTS")) if has_app_context() else False
        ),
    )

    yield {"type": "status", "text": "Thinking"}
    stream_kwargs = {
        "stream_mode": ["messages", "updates"],
        "version": "v2",
        "config": {
            "configurable": {"thread_id": thread.id},
            "callbacks": [tool_call_logger],
        },
    }
    if hasattr(agent, "astream"):
        chunks = agent.astream({"messages": messages}, **stream_kwargs)
    else:
        chunks = _as_async_iterator(agent.stream({"messages": messages}, **stream_kwargs))
    async for chunk in chunks:
        if chunk["type"] == "messages":
            message_chunk, _metadata = chunk["data"]
            if not isinstance(message_chunk, AIMessageChunk):
                continue
            for block in message_chunk.content_blocks:
                if block["type"] == "reasoning" and settings.get("reasoning_summaries_enabled"):
                    text = _reasoning_summary_text(block)
                    if text:
                        yield {"type": "reasoning_summary", "text": text}
                elif block["type"] == "text" and block.get("text"):
                    yield {"type": "token", "text": block["text"]}
        elif chunk["type"] == "updates":
            yield {"type": "status", "text": "Updated agent state"}


async def _as_async_iterator(items: Iterable[dict]) -> AsyncGenerator[dict, None]:
    """Adapt a synchronous iterable for asynchronous consumption.

    Args:
        items: Synchronous stream items.

    Yields:
        Each item without changing its contents.
    """
    for item in items:
        yield item


def _iterate_async_generator(generator: AsyncGenerator[dict, None]) -> Generator[dict, None, None]:
    """Consume an async generator from synchronous application code.

    Args:
        generator: Asynchronous event stream.

    Yields:
        Each event produced by the asynchronous stream.
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(generator.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.run_until_complete(generator.aclose())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


class ReasoningState(TypedDict, total=False):
    """Define state accumulated by the custom reasoning graph."""

    messages: list[dict[str, str]]
    context: str
    plan: str
    draft: str
    alternative_draft: str
    critique: str
    answer: str
    memory_proposal: dict[str, Any]


def _stream_custom_reasoning_graph_response(
    user: User, thread: ChatThread, messages: list[dict[str, str]], settings: dict
) -> Generator[dict, None, None]:
    """Run the application-owned reasoning graph for a chat response.

    Args:
        user: Authenticated user requesting a response.
        thread: Active chat thread.
        messages: Model-ready conversation messages.
        settings: Effective user settings.

    Yields:
        Status, reasoning-summary, answer, and memory-proposal events.
    """
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

    yield {"type": "status", "text": "Running custom reasoning graph"}
    for update in compiled.stream(
        {"messages": messages},
        stream_mode="updates",
        config={"configurable": {"thread_id": thread.id}},
    ):
        for node_name, values in update.items():
            yield {"type": "status", "text": _reasoning_status(node_name)}
            yield from _reasoning_events(values or {}, settings)


def _build_chat_model(settings: dict, *, provider_reasoning: bool) -> Any:
    """Build the configured chat model.

    Args:
        settings: Effective user settings.
        provider_reasoning: Whether to enable provider-native reasoning.

    Returns:
        A configured LangChain chat model.

    Raises:
        ValueError: If the configured provider is unsupported.
    """
    provider = current_app.config.get("CHAT_MODEL_PROVIDER", "openai")
    if provider != "openai":
        raise ValueError(f"Unsupported chat model provider: {provider}")

    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": settings["model_name"],
        "api_key": current_app.config.get("OPENAI_API_KEY") or None,
    }
    if provider_reasoning:
        kwargs["reasoning"] = {
            "effort": settings["reasoning_effort"],
            "summary": "auto" if settings.get("reasoning_summaries_enabled") else None,
        }
    return ChatOpenAI(**kwargs)


def _reasoning_workflow_for_effort(effort: str) -> list[str]:
    """Map a reasoning-effort setting to custom graph node names.

    Args:
        effort: User-selected reasoning effort.

    Returns:
        Ordered graph node names, defaulting to the medium workflow.
    """
    workflows = {
        "minimal": ["answer"],
        "low": ["gather_context", "answer"],
        "medium": ["gather_context", "plan", "answer"],
        "high": ["gather_context", "plan", "draft", "critique", "finalize", "maybe_propose_memory"],
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
    user: User, thread: ChatThread, settings: dict, model: Any
) -> dict[str, Any]:
    """Build closures used as custom reasoning graph nodes.

    Args:
        user: Authenticated user requesting a response.
        thread: Active chat thread.
        settings: Effective user settings.
        model: Chat model invoked by reasoning nodes.

    Returns:
        Node names mapped to callable graph nodes.
    """

    def gather_context(state: ReasoningState) -> dict[str, str]:
        """Gather recent conversation and approved-memory context."""
        context_parts = [_format_recent_context(state["messages"])]
        if settings.get("memory_enabled", True):
            memory_context = _safe_memory_context(user, _last_user_message(state["messages"]))
            if memory_context:
                context_parts.append(memory_context)
        return {"context": "\n\n".join(part for part in context_parts if part)}

    def plan(state: ReasoningState) -> dict[str, str]:
        """Produce a concise, user-safe answer plan."""
        return {
            "plan": _invoke_reasoning_model(
                model,
                (
                    "Create a short, user-safe answer plan. "
                    "Do not reveal hidden chain-of-thought; list only the public approach."
                ),
                _reasoning_prompt(state, "Plan the answer."),
            )
        }

    def answer(state: ReasoningState) -> dict[str, str]:
        """Answer directly for workflows that do not draft and revise."""
        return {
            "answer": _invoke_reasoning_model(
                model,
                _system_prompt(settings),
                _reasoning_prompt(
                    state,
                    "Answer the user's latest message directly. Return only the final answer.",
                ),
            )
        }

    def draft(state: ReasoningState) -> dict[str, str]:
        """Create an initial answer draft for higher-effort workflows."""
        return {
            "draft": _invoke_reasoning_model(
                model,
                _system_prompt(settings),
                _reasoning_prompt(
                    state,
                    "Write a strong draft answer. It can be improved later.",
                ),
            )
        }

    def alternative_draft(state: ReasoningState) -> dict[str, str]:
        """Create a structurally different draft for comparison."""
        return {
            "alternative_draft": _invoke_reasoning_model(
                model,
                _system_prompt(settings),
                _reasoning_prompt(
                    state,
                    "Write an alternate draft with a different organization or emphasis.",
                ),
            )
        }

    def critique(state: ReasoningState) -> dict[str, str]:
        """Check answer drafts for correctness, omissions, and clarity."""
        return {
            "critique": _invoke_reasoning_model(
                model,
                (
                    "Critique the draft answer for correctness, missing caveats, "
                    "unsupported claims, and clarity. Keep it concise and user-safe."
                ),
                _reasoning_prompt(state, "Review the draft before finalizing."),
            )
        }

    def finalize(state: ReasoningState) -> dict[str, str]:
        """Revise the selected draft using the critique."""
        return {
            "answer": _invoke_reasoning_model(
                model,
                _system_prompt(settings),
                _reasoning_prompt(
                    state,
                    "Revise using the critique. Return only the final user-facing answer.",
                ),
            )
        }

    def maybe_propose_memory(state: ReasoningState) -> dict[str, Any]:
        """Persist a reviewable memory when the prompt and settings allow it."""
        prompt = _last_user_message(state["messages"])
        if not _should_create_memory_proposal(prompt, settings):
            return {}
        proposal = _create_memory_proposal(user, thread, prompt, "user_request", 0.6)
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


def _invoke_reasoning_model(model: Any, system_prompt: str, user_prompt: str) -> str:
    """Invoke a reasoning node's chat model and normalize its text.

    Args:
        model: Chat model exposing an ``invoke`` method.
        system_prompt: Instruction supplied as the system message.
        user_prompt: State and task supplied as the user message.

    Returns:
        Normalized response text.
    """
    response = model.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    return _message_text(response)


def _reasoning_events(values: dict[str, Any], settings: dict) -> Generator[dict, None, None]:
    """Translate custom graph state updates into client-facing events.

    Args:
        values: State fields returned by the completed graph node.
        settings: Effective user settings.

    Yields:
        Reasoning-summary, answer-token, and memory-proposal events.
    """
    if settings.get("reasoning_summaries_enabled"):
        if plan := values.get("plan"):
            yield {"type": "reasoning_summary", "text": f"Plan:\n\n{plan}\n\n"}
        if critique := values.get("critique"):
            yield {"type": "reasoning_summary", "text": f"Self-check:\n\n{critique}\n\n"}
    if answer := values.get("answer"):
        yield {"type": "token", "text": answer}
    if memory_proposal := values.get("memory_proposal"):
        yield {"type": "memory_proposal", **memory_proposal}


def _reasoning_status(node_name: str) -> str:
    """Return a user-facing status for a custom graph node.

    Args:
        node_name: Completed graph node name.

    Returns:
        A concise progress message.
    """
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
    """Build a model prompt from the accumulated reasoning state.

    Args:
        state: Current custom graph state.
        instruction: Task for the next reasoning node.

    Returns:
        A plain-text prompt containing available state fields.
    """
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
    """Format at most six recent messages as context.

    Args:
        messages: Model-ready conversation messages.

    Returns:
        Labeled recent context, or an empty string.
    """
    recent = messages[-6:]
    if not recent:
        return ""
    return "Recent conversation:\n" + _format_messages(recent)


def _format_messages(messages: list[dict[str, str]]) -> str:
    """Format model-ready messages as role-prefixed lines.

    Args:
        messages: Conversation messages with role and content fields.

    Returns:
        Newline-delimited message text.
    """
    return "\n".join(f"{message['role']}: {message['content']}" for message in messages)


def _last_user_message(messages: list[dict[str, str]]) -> str:
    """Return the content of the newest user message.

    Args:
        messages: Model-ready conversation messages.

    Returns:
        Latest user content, or an empty string.
    """
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _safe_memory_context(user: User, query: str) -> str:
    """Retrieve approved memory context without failing a chat response.

    Args:
        user: User whose memories may be recalled.
        query: Retrieval query.

    Returns:
        Formatted memory context, or an empty string on failure.
    """
    try:
        memories = search_long_term_memories(user.id, query, limit=5)
    except Exception:  # noqa: BLE001 - optional memory must never prevent a response.
        return ""
    formatted = format_memory_search_results(memories)
    return f"Approved long-term memory:\n{formatted}" if formatted else ""


def _message_text(response: Any) -> str:
    """Normalize common chat-model response content into text.

    Args:
        response: Chat message, content block collection, or scalar value.

    Returns:
        Concatenated response text.
    """
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def _reasoning_summary_text(block: dict[str, Any]) -> str:
    """Extract summary text from current and legacy reasoning blocks.

    Args:
        block: Provider reasoning content block.

    Returns:
        Extracted summary text, or an empty string.
    """
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
    """Check whether a prompt should create a reviewable memory proposal.

    Args:
        prompt: Latest user message.
        settings: Effective user settings.

    Returns:
        Whether memory is enabled, proposals are allowed, and the user asked
        the assistant to remember something.
    """
    return (
        settings.get("memory_enabled", True)
        and settings.get("privacy", {}).get("allow_memory_proposals", True)
        and "remember" in prompt.lower()
    )


def _conversation_messages(user: User, thread: ChatThread, prompt: str) -> list[dict[str, str]]:
    """Build model messages from rolling summary and recent persisted turns.

    Args:
        user: Owner of the conversation.
        thread: Active chat thread.
        prompt: Latest user prompt, which may already be persisted.

    Returns:
        Chronological model-ready messages with optional summary context.
    """
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
    """Build memory tools scoped to the authenticated user and thread.

    Args:
        user: Authenticated user allowed to invoke the tools.
        thread: Active chat thread used for memory provenance.
        settings: Effective user settings controlling tool behavior.

    Returns:
        Memory recall and proposal tools.
    """
    from langchain.tools import tool

    @tool
    def recall_user_memory(query: str) -> str:
        """Retrieve approved long-term memories with vector similarity search.

        Args:
            query: Natural-language memory search query.

        Returns:
            Formatted matching memories or a disabled message.
        """
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        return format_memory_search_results(search_long_term_memories(user.id, query, limit=5))

    @tool
    def propose_memory(
        memory_text: str, category: str = "preference", confidence: float = 0.5
    ) -> str:
        """Create a memory proposal that requires user approval before indexing.

        Args:
            memory_text: Stable preference or fact worth remembering.
            category: Short classification for the proposed memory.
            confidence: Confidence value clamped between zero and one.

        Returns:
            A proposal identifier or a settings-based disabled message.
        """
        if not settings.get("memory_enabled", True):
            return "Memory is disabled for this user."
        if not settings.get("privacy", {}).get("allow_memory_proposals", True):
            return "The user has disabled memory proposals."
        proposal = _create_memory_proposal(
            user,
            thread,
            memory_text,
            category,
            confidence,
        )
        return f"Created memory proposal {proposal.id} for user review."

    return [recall_user_memory, propose_memory]


def _create_memory_proposal(
    user: User,
    thread: ChatThread,
    memory_text: str,
    category: str,
    confidence: float,
) -> PendingMemory:
    """Normalize and persist a user-reviewable memory proposal.

    Args:
        user: User who owns the proposal.
        thread: Thread from which the proposal originated.
        memory_text: Proposed memory content.
        category: Proposed memory classification.
        confidence: Proposal confidence, which is clamped to a valid range.

    Returns:
        The persisted proposal.
    """
    proposal = PendingMemory(
        user_id=user.id,
        source_thread_id=thread.id,
        memory_text=memory_text[:4000],
        category=category[:80] or "preference",
        confidence=max(0.0, min(float(confidence), 1.0)),
    )
    db.session.add(proposal)
    db.session.commit()
    return proposal


def _stream_demo_response(
    user: User,
    thread: ChatThread,
    prompt: str,
    settings: dict,
    _messages: list[dict[str, str]],
) -> Generator[dict, None, None]:
    """Stream a deterministic local response when no API key is configured.

    Args:
        user: Authenticated user requesting a response.
        thread: Active chat thread.
        prompt: Latest user prompt.
        settings: Effective user settings.
        _messages: Prepared messages, unused by deterministic demo mode.

    Yields:
        Demo status, reasoning-summary, token, and memory-proposal events.
    """
    yield {"type": "status", "text": "Demo mode: set OPENAI_API_KEY to use LangGraph with OpenAI."}
    if settings.get("reasoning_summaries_enabled"):
        yield {
            "type": "reasoning_summary",
            "text": (
                "No API key is configured, so this local demo response validates streaming only."
            ),
        }
    text = (
        "I am running in **local demo mode** because no OpenAI API key is configured.\n\n"
        "Once `OPENAI_API_KEY` and Postgres are configured, this endpoint streams LangGraph "
        "messages, tool progress, reasoning summaries, and user-approved memory proposals."
    )
    for token in re.split(r"(\s+)", text):
        if token:
            yield {"type": "token", "text": token}
    if _should_create_memory_proposal(prompt, settings):
        proposal = _create_memory_proposal(user, thread, prompt, "user_request", 0.6)
        yield {
            "type": "memory_proposal",
            "id": proposal.id,
            "memory_text": proposal.memory_text,
            "category": proposal.category,
            "confidence": proposal.confidence,
        }


def _system_prompt(settings: dict[str, Any], mcp_namespaces: tuple[str, ...] = ()) -> str:
    """Build the agent's system prompt from settings and tool access.

    Args:
        settings: Effective user settings.
        mcp_namespaces: Authorized MCP namespace names loaded for this run.

    Returns:
        Complete system instructions for the chat agent.
    """
    mcp_guidance = (
        " No external-data tools are available for this request. Be transparent about that "
        "limitation when current or external information is required."
    )
    if mcp_namespaces:
        namespace_list = ", ".join(mcp_namespaces)
        mcp_guidance = (
            f" The authenticated user can use tools from these MCP namespaces: {namespace_list}. "
            "MCP tool names begin with mcp_<namespace>_; select only a tool from the namespace "
            "that owns the requested data or operation. Use MCP tools for all external data and "
            "actions; there is no built-in web search or URL-fetching fallback."
        )
    return (
        "You are a helpful chatbot. Respond in clean GitHub-flavored Markdown. "
        "Use recall_user_memory as a RAG retriever over approved long-term memories "
        "when user-specific remembered context would help. "
        "Use the rolling conversation summary plus prior messages in this thread as "
        "short-term conversation memory; if the user refers to something earlier in the "
        "same chat, answer from that context. "
        "Use propose_memory only for stable user preferences or facts worth remembering, "
        "and understand that the user must approve every proposed memory before it persists. "
        f"{mcp_guidance} "
        f"User settings: compact_mode={settings.get('compact_mode')}, "
        f"font_size={settings.get('font_size')}."
    )
