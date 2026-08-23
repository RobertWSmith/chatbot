from __future__ import annotations

import asyncio
import re
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Group, GroupMCPNamespace, GroupMembership, MCPNamespace
from app.services.mcp_access import accessible_mcp_namespaces, probe_mcp_namespace

bp = Blueprint("tenancy", __name__)

PORTAL_CONTAINER_URL = "http://mcp-portal:8001/mcp"
PORTAL_HOST_URL = "http://localhost:8001/mcp"

_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,30}$")
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_ALLOWED_TRANSPORTS = {"http", "streamable_http", "sse"}
_SECRET_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def platform_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        configured_admin = current_user.email.lower() in current_app.config.get(
            "PLATFORM_ADMIN_EMAILS", ()
        )
        if configured_admin and not current_user.is_platform_admin:
            current_user.is_platform_admin = True
            db.session.commit()
        if not current_user.is_platform_admin:
            if request.path.startswith("/api/"):
                return (
                    jsonify({"error": "Platform administrator access is required."}),
                    403,
                )
            return render_template("errors/admin_forbidden.html"), 403
        return view(*args, **kwargs)

    return login_required(wrapped)


@bp.get("/admin/mcp")
@platform_admin_required
def admin_mcp_page():
    return render_template(
        "admin/mcp.html",
        namespaces=MCPNamespace.query.order_by(MCPNamespace.display_name.asc()).all(),
        groups=Group.query.order_by(Group.name.asc()).all(),
        portal_container_url=PORTAL_CONTAINER_URL,
        portal_host_url=PORTAL_HOST_URL,
    )


@bp.get("/api/admin/mcp-namespaces")
@platform_admin_required
def list_mcp_namespaces():
    items = MCPNamespace.query.order_by(MCPNamespace.display_name.asc()).all()
    return jsonify({"mcp_namespaces": [_admin_namespace_json(item) for item in items]})


@bp.post("/api/admin/mcp-namespaces")
@platform_admin_required
def create_mcp_namespace():
    payload = request.get_json(silent=True) or {}
    namespace = str(payload.get("namespace") or "").strip().lower()
    display_name = str(payload.get("display_name") or namespace).strip()
    description = str(payload.get("description") or "").strip()
    transport = str(payload.get("transport") or "http").strip().lower()
    url = str(payload.get("url") or "").strip()
    auth_token_env_var = str(payload.get("auth_token_env_var") or "").strip() or None
    headers = payload.get("headers") or {}

    error = _validate_namespace_payload(
        namespace,
        display_name,
        description,
        transport,
        url,
        auth_token_env_var,
        headers,
    )
    if error:
        return jsonify({"error": error}), 400
    if MCPNamespace.query.filter_by(namespace=namespace).first():
        return jsonify({"error": "That MCP namespace already exists."}), 409

    item = MCPNamespace(
        namespace=namespace,
        display_name=display_name,
        description=description,
        transport=transport,
        url=url,
        auth_token_env_var=auth_token_env_var,
        headers=headers,
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "That MCP namespace already exists."}), 409
    return jsonify({"mcp_namespace": _admin_namespace_json(item)}), 201


@bp.post("/api/admin/mcp-portal/bootstrap")
@platform_admin_required
def bootstrap_local_mcp_portal():
    """Register the Compose portal and grant it to the signed-in administrator."""
    item = MCPNamespace.query.filter_by(namespace="portal").first()
    if item is None:
        item = MCPNamespace(namespace="portal")
        db.session.add(item)
    item.display_name = "MCP Portal"
    item.description = (
        "Local Compose MCP portal with public web search and link resolution."
    )
    item.transport = "streamable_http"
    item.url = PORTAL_CONTAINER_URL
    item.auth_token_env_var = None
    item.headers = {}
    item.enabled = True
    db.session.flush()

    owner_membership = (
        GroupMembership.query.filter_by(user_id=current_user.id, role="owner")
        .order_by(GroupMembership.created_at.asc())
        .first()
    )
    group = owner_membership.group if owner_membership is not None else None
    if group is None:
        group_slug = f"mcp-portal-{current_user.id}"
        group = Group(
            name="My MCP Portal access",
            slug=group_slug,
            created_by_user_id=current_user.id,
        )
        db.session.add(group)
        db.session.flush()

    membership = GroupMembership.query.filter_by(
        group_id=group.id, user_id=current_user.id
    ).first()
    if membership is None:
        db.session.add(
            GroupMembership(group_id=group.id, user_id=current_user.id, role="owner")
        )

    grant = GroupMCPNamespace.query.filter_by(
        group_id=group.id, mcp_namespace_id=item.id
    ).first()
    if grant is None:
        db.session.add(GroupMCPNamespace(group_id=group.id, mcp_namespace_id=item.id))

    legacy_urls = {
        PORTAL_CONTAINER_URL,
        PORTAL_HOST_URL,
        "http://localhost:8000/mcp",
    }
    duplicates = MCPNamespace.query.filter(MCPNamespace.id != item.id).all()
    for duplicate in duplicates:
        if (
            not duplicate.namespace.startswith("portal")
            or duplicate.url not in legacy_urls
        ):
            continue
        for duplicate_grant in duplicate.group_grants:
            existing_grant = GroupMCPNamespace.query.filter_by(
                group_id=duplicate_grant.group_id,
                mcp_namespace_id=item.id,
            ).first()
            if existing_grant is None:
                db.session.add(
                    GroupMCPNamespace(
                        group_id=duplicate_grant.group_id,
                        mcp_namespace_id=item.id,
                    )
                )
        duplicate.enabled = False
    db.session.commit()
    return jsonify(
        {
            "mcp_namespace": _admin_namespace_json(item),
            "group": {"id": group.id, "name": group.name, "slug": group.slug},
        }
    )


@bp.post("/api/admin/mcp-namespaces/<namespace>/test")
@platform_admin_required
def test_mcp_namespace(namespace: str):
    item = MCPNamespace.query.filter_by(namespace=namespace.lower()).first_or_404()
    try:
        tools = asyncio.run(probe_mcp_namespace(item))
    except Exception as exc:  # noqa: BLE001 - return a safe admin diagnostic
        current_app.logger.warning(
            "MCP probe failed for %s: %s", item.namespace, type(exc).__name__
        )
        return (
            jsonify(
                {
                    "error": "The MCP server could not be reached or did not return tools.",
                    "error_type": type(exc).__name__,
                }
            ),
            502,
        )
    return jsonify(
        {"namespace": item.namespace, "tools": tools, "tool_count": len(tools)}
    )


@bp.post("/api/groups")
@login_required
def create_group():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or _slugify(name)).strip().lower()
    if not name or len(name) > 120:
        return (
            jsonify({"error": "Group name must be between 1 and 120 characters."}),
            400,
        )
    if not _SLUG_PATTERN.fullmatch(slug):
        return (
            jsonify(
                {
                    "error": "Group slug must contain lowercase letters, numbers, or hyphens."
                }
            ),
            400,
        )
    if Group.query.filter_by(slug=slug).first():
        return jsonify({"error": "That group slug is already in use."}), 409

    group = Group(name=name, slug=slug, created_by_user_id=current_user.id)
    group.memberships.append(GroupMembership(user_id=current_user.id, role="owner"))
    db.session.add(group)
    db.session.commit()
    return (
        jsonify({"group": {"id": group.id, "name": group.name, "slug": group.slug}}),
        201,
    )


@bp.put("/api/admin/groups/<group_id>/mcp-namespaces/<namespace>")
@platform_admin_required
def grant_group_mcp_namespace(group_id: str, namespace: str):
    group = db.session.get(Group, group_id)
    item = MCPNamespace.query.filter_by(namespace=namespace.lower()).first()
    if group is None or item is None:
        abort(404)
    grant = GroupMCPNamespace.query.filter_by(
        group_id=group.id, mcp_namespace_id=item.id
    ).first()
    if grant is None:
        db.session.add(GroupMCPNamespace(group_id=group.id, mcp_namespace_id=item.id))
        db.session.commit()
    return jsonify({"group_id": group.id, "mcp_namespace": item.namespace})


@bp.delete("/api/admin/groups/<group_id>/mcp-namespaces/<namespace>")
@platform_admin_required
def revoke_group_mcp_namespace(group_id: str, namespace: str):
    item = MCPNamespace.query.filter_by(namespace=namespace.lower()).first()
    if item is None:
        abort(404)
    grant = GroupMCPNamespace.query.filter_by(
        group_id=group_id, mcp_namespace_id=item.id
    ).first()
    if grant is None:
        abort(404)
    db.session.delete(grant)
    db.session.commit()
    return "", 204


@bp.get("/api/me/mcp-namespaces")
@login_required
def list_my_mcp_namespaces():
    items = accessible_mcp_namespaces(current_user.id)
    return jsonify(
        {
            "mcp_namespaces": [
                {
                    "namespace": item.namespace,
                    "display_name": item.display_name,
                    "description": item.description,
                }
                for item in items
            ]
        }
    )


def _validate_namespace_payload(
    namespace: str,
    display_name: str,
    description: str,
    transport: str,
    url: str,
    auth_token_env_var: str | None,
    headers: object,
) -> str | None:
    if not _NAMESPACE_PATTERN.fullmatch(namespace):
        return "Namespace must be 2-31 lowercase letters or numbers and start with a letter."
    if not display_name or len(display_name) > 120:
        return "Display name must be between 1 and 120 characters."
    if len(description) > 2000:
        return "Description must be no more than 2000 characters."
    if transport not in _ALLOWED_TRANSPORTS:
        return "Transport must be http, streamable_http, or sse."
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "MCP URL must be an absolute HTTP or HTTPS URL."
    if auth_token_env_var and not _ENV_VAR_PATTERN.fullmatch(auth_token_env_var):
        return "auth_token_env_var must be a valid uppercase environment variable name."
    if not isinstance(headers, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in headers.items()
    ):
        return "Headers must be an object containing string values."
    if any(key.lower() in _SECRET_HEADERS for key in headers):
        return "Secret authentication headers must use auth_token_env_var."
    return None


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:80]


def _admin_namespace_json(item: MCPNamespace) -> dict:
    return {
        "namespace": item.namespace,
        "display_name": item.display_name,
        "description": item.description,
        "transport": item.transport,
        "url": item.url,
        "auth_token_env_var": item.auth_token_env_var,
        "headers": item.headers,
        "enabled": item.enabled,
        "group_grant_count": len(item.group_grants),
    }
