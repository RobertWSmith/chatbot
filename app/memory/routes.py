from __future__ import annotations

from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required

from app.extensions import db
from app.models import LongTermMemory, PendingMemory
from app.services.rag_memory import approve_memory_to_rag

bp = Blueprint("memory", __name__)


@bp.get("/memory")
@login_required
def memory_page():
    """Render pending and approved memories for the current user."""
    memories = (
        PendingMemory.query.filter_by(user_id=current_user.id)
        .order_by(PendingMemory.created_at.desc())
        .all()
    )
    approved_memories = (
        LongTermMemory.query.filter_by(user_id=current_user.id)
        .order_by(LongTermMemory.created_at.desc())
        .all()
    )
    return render_template("memory.html", memories=memories, approved_memories=approved_memories)


@bp.get("/api/memories")
@login_required
def list_memories():
    """Return the current user's proposed memories as JSON."""
    rows = PendingMemory.query.filter_by(user_id=current_user.id).order_by(
        PendingMemory.created_at.desc()
    )
    return jsonify({"memories": [_serialize_memory(row) for row in rows]})


@bp.post("/api/memories/<memory_id>/approve")
@login_required
def approve_memory(memory_id: str):
    """Approve a pending proposal and index it for retrieval.

    Args:
        memory_id: Pending-memory identifier.
    """
    memory = _get_user_memory_or_404(memory_id)
    if memory.status != "pending":
        return jsonify({"error": "Only pending memories can be approved."}), 409
    memory.status = "approved"
    long_term_memory = approve_memory_to_rag(memory)
    db.session.flush()
    memory.approved_memory_key = long_term_memory.id
    db.session.commit()
    return jsonify({"memory": _serialize_memory(memory)})


@bp.post("/api/memories/<memory_id>/reject")
@login_required
def reject_memory(memory_id: str):
    """Reject one of the current user's pending memory proposals.

    Args:
        memory_id: Pending-memory identifier.
    """
    memory = _get_user_memory_or_404(memory_id)
    if memory.status != "pending":
        return jsonify({"error": "Only pending memories can be rejected."}), 409
    memory.status = "rejected"
    db.session.commit()
    return jsonify({"memory": _serialize_memory(memory)})


def _get_user_memory_or_404(memory_id: str) -> PendingMemory:
    """Load a user-owned memory proposal or raise a 404 response.

    Args:
        memory_id: Pending-memory identifier.

    Returns:
        The matching user-owned proposal.
    """
    return PendingMemory.query.filter_by(id=memory_id, user_id=current_user.id).first_or_404()


def _serialize_memory(memory: PendingMemory) -> dict:
    """Serialize a pending memory for an API response.

    Args:
        memory: Proposal to serialize.

    Returns:
        JSON-compatible memory fields.
    """
    return {
        "id": memory.id,
        "memory_text": memory.memory_text,
        "category": memory.category,
        "confidence": memory.confidence,
        "status": memory.status,
        "approved_memory_key": memory.approved_memory_key,
        "created_at": memory.created_at.isoformat(),
    }
