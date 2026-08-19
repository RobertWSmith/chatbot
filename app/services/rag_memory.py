from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import LongTermMemory, PendingMemory, utcnow


@dataclass(frozen=True)
class MemorySearchResult:
    """Pair an approved memory with its query similarity score."""

    memory: LongTermMemory
    score: float


def approve_memory_to_rag(proposal: PendingMemory) -> LongTermMemory:
    """Create an indexed long-term memory from an approved proposal.

    Args:
        proposal: User-approved pending memory.

    Returns:
        The existing or newly staged long-term memory.
    """
    existing = LongTermMemory.query.filter_by(source_pending_memory_id=proposal.id).one_or_none()
    if existing:
        return existing
    embedding = embed_memory_text(proposal.memory_text)
    memory = LongTermMemory(
        user_id=proposal.user_id,
        source_pending_memory_id=proposal.id,
        text=proposal.memory_text,
        category=proposal.category,
        confidence=proposal.confidence,
        embedding_model=current_app.config["MEMORY_EMBEDDING_MODEL"],
        embedding=embedding,
        memory_metadata={
            "source": "user_approved_proposal",
            "source_thread_id": proposal.source_thread_id,
            "source_message_id": proposal.source_message_id,
        },
    )
    db.session.add(memory)
    return memory


def search_long_term_memories(user_id: int, query: str, limit: int = 5) -> list[MemorySearchResult]:
    """Retrieve a user's approved memories by embedding similarity.

    Args:
        user_id: Owner whose memories may be searched.
        query: Natural-language retrieval query.
        limit: Maximum number of results.

    Returns:
        Results ordered from most to least similar.
    """
    query_embedding = embed_memory_text(query)
    if db.session.bind and db.session.bind.dialect.name == "postgresql":
        distance = LongTermMemory.embedding.cosine_distance(query_embedding).label("distance")
        rows = (
            db.session.query(LongTermMemory, distance)
            .filter(LongTermMemory.user_id == user_id)
            .order_by(distance)
            .limit(limit)
            .all()
        )
        results = [MemorySearchResult(memory=row[0], score=1.0 - float(row[1])) for row in rows]
    else:
        memories = LongTermMemory.query.filter_by(user_id=user_id).all()
        results = sorted(
            (
                MemorySearchResult(
                    memory=memory,
                    score=cosine_similarity(query_embedding, memory.embedding),
                )
                for memory in memories
            ),
            key=lambda result: result.score,
            reverse=True,
        )[:limit]

    now = utcnow()
    for result in results:
        result.memory.last_retrieved_at = now
        result.memory.retrieval_count += 1
    if results:
        db.session.commit()
    return results


def format_memory_search_results(results: list[MemorySearchResult]) -> str:
    """Format memory search results for model context.

    Args:
        results: Ranked memory search results.

    Returns:
        A compact textual list, or a no-results message.
    """
    if not results:
        return "No relevant long-term memories found."
    lines = []
    for result in results:
        lines.append(
            "- "
            f"[{result.memory.category}; confidence {result.memory.confidence:.0%}; "
            f"similarity {result.score:.2f}] {result.memory.text}"
        )
    return "\n".join(lines)


def embed_memory_text(text: str) -> list[float]:
    """Embed memory text with OpenAI or a deterministic local fallback.

    Args:
        text: Memory or query text.

    Returns:
        A normalized embedding vector.
    """
    model = current_app.config["MEMORY_EMBEDDING_MODEL"]
    dimensions = int(current_app.config["MEMORY_EMBEDDING_DIMENSIONS"])
    api_key = current_app.config.get("OPENAI_API_KEY")
    if api_key:
        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(model=model, dimensions=dimensions, api_key=api_key)
        return embeddings.embed_query(text)
    return deterministic_embedding(text, dimensions)


def deterministic_embedding(text: str, dimensions: int) -> list[float]:
    """Create a stable normalized embedding for local development and tests.

    Args:
        text: Text used as the deterministic seed.
        dimensions: Required vector length.

    Returns:
        A normalized vector with the requested number of dimensions.
    """
    values = []
    seed = text.encode("utf-8")
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Calculate cosine similarity across the shared vector dimensions.

    Args:
        left: First vector.
        right: Second vector.

    Returns:
        Cosine similarity, or ``0.0`` when either vector is empty.
    """
    left_values = list(left) if left is not None else []
    right_values = list(right) if right is not None else []
    if not left_values or not right_values:
        return 0.0
    length = min(len(left_values), len(right_values))
    dot = sum(left_values[index] * right_values[index] for index in range(length))
    left_norm = math.sqrt(sum(value * value for value in left_values[:length])) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right_values[:length])) or 1.0
    return dot / (left_norm * right_norm)
