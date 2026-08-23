from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.services.mcp_access import accessible_mcp_namespaces
from app.validation import (
    MODEL_OPTIONS,
    REASONING_OPTIONS,
    REASONING_PROVIDER_OPTIONS,
    validate_settings_update,
)

bp = Blueprint("settings", __name__)


@bp.get("/settings")
@login_required
def settings_page():
    return render_template(
        "settings.html",
        settings=current_user.settings.merged(),
        model_options=MODEL_OPTIONS,
        reasoning_options=REASONING_OPTIONS,
        reasoning_provider_options=REASONING_PROVIDER_OPTIONS,
        mcp_namespaces=accessible_mcp_namespaces(current_user.id),
    )


@bp.patch("/api/settings")
@login_required
def update_settings():
    patch = request.get_json(silent=True) or {}
    try:
        settings = validate_settings_update(current_user.settings.merged(), patch)
    except ValueError as exc:
        return jsonify({"errors": exc.args[0]}), 400
    current_user.settings.system_prompt = settings.pop("system_prompt")
    current_user.settings.data = settings
    db.session.commit()
    return jsonify({"settings": current_user.settings.merged()})
