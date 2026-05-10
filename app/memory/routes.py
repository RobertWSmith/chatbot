from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required

from app.extensions import db
from app.models import PendingMemory

from .store import open_memory_store, user_memory_namespace

bp = Blueprint("memory", __name__)


@bp.get("/memory")
@login_required
def memory_page():
    memories = PendingMemory.query.filter_by(user_id=current_user.id).order_by(
        PendingMemory.created_at.desc()
    )
    return render_template("memory.html", memories=memories)


@bp.get("/api/memories")
@login_required
def list_memories():
    rows = PendingMemory.query.filter_by(user_id=current_user.id).order_by(
        PendingMemory.created_at.desc()
    )
    return jsonify({"memories": [_serialize_memory(row) for row in rows]})


@bp.post("/api/memories/<memory_id>/approve")
@login_required
def approve_memory(memory_id: str):
    memory = _get_user_memory_or_404(memory_id)
    if memory.status != "pending":
        return jsonify({"error": "Only pending memories can be approved."}), 409
    memory.status = "approved"
    memory.approved_memory_key = str(uuid.uuid4())
    with open_memory_store() as store:
        if store is not None:
            store.put(
                user_memory_namespace(current_user.id),
                memory.approved_memory_key,
                {
                    "text": memory.memory_text,
                    "category": memory.category,
                    "confidence": memory.confidence,
                },
            )
    db.session.commit()
    return jsonify({"memory": _serialize_memory(memory)})


@bp.post("/api/memories/<memory_id>/reject")
@login_required
def reject_memory(memory_id: str):
    memory = _get_user_memory_or_404(memory_id)
    if memory.status != "pending":
        return jsonify({"error": "Only pending memories can be rejected."}), 409
    memory.status = "rejected"
    db.session.commit()
    return jsonify({"memory": _serialize_memory(memory)})


def _get_user_memory_or_404(memory_id: str) -> PendingMemory:
    return PendingMemory.query.filter_by(id=memory_id, user_id=current_user.id).first_or_404()


def _serialize_memory(memory: PendingMemory) -> dict:
    return {
        "id": memory.id,
        "memory_text": memory.memory_text,
        "category": memory.category,
        "confidence": memory.confidence,
        "status": memory.status,
        "approved_memory_key": memory.approved_memory_key,
        "created_at": memory.created_at.isoformat(),
    }
