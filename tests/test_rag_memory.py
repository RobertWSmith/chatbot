from app.extensions import db
from app.models import LongTermMemory, PendingMemory, User
from app.services.rag_memory import search_long_term_memories

from .conftest import register


def test_memory_approval_indexes_long_term_rag_memory(client, app):
    """Ensure approving a proposal creates an indexed long-term memory."""
    register(client)
    with app.app_context():
        proposal = PendingMemory.query.filter_by(status="pending").one_or_none()
        if proposal is None:
            user_id = User.query.filter_by(email="user@example.com").one().id
            proposal = PendingMemory(
                user_id=user_id,
                memory_text="The user likes concise answers with practical examples.",
                category="preference",
                confidence=0.8,
            )
            db.session.add(proposal)
            db.session.commit()
        proposal_id = proposal.id

    response = client.post(f"/api/memories/{proposal_id}/approve")

    assert response.status_code == 200
    with app.app_context():
        proposal = db.session.get(PendingMemory, proposal_id)
        memory = LongTermMemory.query.filter_by(source_pending_memory_id=proposal_id).one()
        assert proposal.status == "approved"
        assert proposal.approved_memory_key == memory.id
        assert memory.text == proposal.memory_text
        assert len(memory.embedding) == app.config["MEMORY_EMBEDDING_DIMENSIONS"]


def test_memory_page_renders_pending_and_approved_memory(client, app):
    """Ensure the memory page renders pending and approved entries."""
    register(client)
    with app.app_context():
        user_id = User.query.filter_by(email="user@example.com").one().id
        db.session.add(
            LongTermMemory(
                user_id=user_id,
                text="The user likes concise answers.",
                category="preference",
                confidence=0.9,
                embedding_model=app.config["MEMORY_EMBEDDING_MODEL"],
                embedding=[0.0] * app.config["MEMORY_EMBEDDING_DIMENSIONS"],
            )
        )
        db.session.commit()

    response = client.get("/memory")

    assert response.status_code == 200
    assert b"Approved RAG Memory" in response.data
    assert b"The user likes concise answers." in response.data


def test_rag_memory_search_returns_relevant_approved_memory(app):
    """Ensure retrieval ranks a relevant approved memory for its owner."""
    with app.app_context():
        first = LongTermMemory(
            user_id=42,
            text="The user prefers concise answers with examples.",
            category="preference",
            confidence=0.9,
            embedding_model=app.config["MEMORY_EMBEDDING_MODEL"],
            embedding=[1.0] + [0.0] * (app.config["MEMORY_EMBEDDING_DIMENSIONS"] - 1),
        )
        second = LongTermMemory(
            user_id=42,
            text="The user is planning a long vacation.",
            category="fact",
            confidence=0.7,
            embedding_model=app.config["MEMORY_EMBEDDING_MODEL"],
            embedding=[0.0, 1.0] + [0.0] * (app.config["MEMORY_EMBEDDING_DIMENSIONS"] - 2),
        )
        db.session.add_all([first, second])
        db.session.commit()

        results = search_long_term_memories(42, "concise answers examples", limit=2)

        assert len(results) == 2
        assert results[0].memory.user_id == 42
        assert results[0].memory.retrieval_count == 1
