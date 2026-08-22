from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.validation import MODEL_OPTIONS, REASONING_OPTIONS, validate_settings_update

bp = Blueprint("settings", __name__)


@bp.get("/settings")
@login_required
def settings_page():
    """Render the current user's effective settings."""
    return render_template(
        "settings.html",
        settings=current_user.settings.merged(),
        model_options=MODEL_OPTIONS,
        reasoning_options=REASONING_OPTIONS,
    )


@bp.patch("/api/settings")
@login_required
def update_settings():
    """Validate and persist a partial settings update."""
    patch = request.get_json(silent=True) or {}
    try:
        current_user.settings.data = validate_settings_update(current_user.settings.merged(), patch)
    except ValueError as exc:
        return jsonify({"errors": exc.args[0]}), 400
    db.session.commit()
    return jsonify({"settings": current_user.settings.merged()})
