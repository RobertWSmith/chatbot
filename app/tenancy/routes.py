from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Group,
    GroupInvitation,
    GroupMCPNamespace,
    GroupMembership,
    MCPNamespace,
    utcnow,
)
from app.services.mcp_access import accessible_mcp_namespaces

from .schemas import (
    ErrorResponse,
    MCPNamespaceCreate,
    MCPNamespaceEnvelope,
    MCPNamespaceListResponse,
    MCPNamespaceResponse,
)

bp = Blueprint("tenancy", __name__)

_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


def platform_admin_required(view):
    """Require login and platform-administrator status for a view.

    Args:
        view: Flask view function to protect.

    Returns:
        A decorated view that returns the appropriate API or HTML denial.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        """Authorize the request before invoking the protected view."""
        if (
            not current_user.is_platform_admin
            and current_user.email.lower() in current_app.config.get("PLATFORM_ADMIN_EMAILS", ())
        ):
            current_user.is_platform_admin = True
            db.session.commit()
        if not current_user.is_platform_admin:
            if request.path.startswith("/api/"):
                response = ErrorResponse(error="Platform administrator access is required.")
                return jsonify(response.model_dump(mode="json")), 403
            return render_template("errors/admin_forbidden.html"), 403
        return view(*args, **kwargs)

    return login_required(wrapped)


@bp.get("/admin/mcp")
@platform_admin_required
def admin_mcp_page():
    """Render the MCP namespace administration page."""
    namespaces = MCPNamespace.query.order_by(MCPNamespace.display_name.asc()).all()
    return render_template("admin/mcp.html", namespaces=namespaces)


@bp.get("/api/admin/mcp-namespaces")
@platform_admin_required
def list_mcp_namespaces():
    """Return every registered MCP namespace to an administrator."""
    namespaces = MCPNamespace.query.order_by(MCPNamespace.display_name.asc()).all()
    response = MCPNamespaceListResponse(
        mcp_namespaces=[_admin_namespace_model(item) for item in namespaces]
    )
    return jsonify(response.model_dump(mode="json"))


@bp.get("/api/groups")
@login_required
def list_groups():
    """Return the current user's groups and namespace grants."""
    memberships = (
        GroupMembership.query.filter_by(user_id=current_user.id)
        .join(Group)
        .order_by(Group.name.asc())
        .all()
    )
    return jsonify(
        {
            "groups": [
                {
                    "id": membership.group.id,
                    "name": membership.group.name,
                    "slug": membership.group.slug,
                    "role": membership.role,
                    "mcp_namespaces": sorted(
                        grant.mcp_namespace.namespace
                        for grant in membership.group.namespace_grants
                        if grant.mcp_namespace.enabled
                    ),
                }
                for membership in memberships
            ]
        }
    )


@bp.post("/api/groups")
@login_required
def create_group():
    """Create a tenant group owned by the current user."""
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or _slugify(name)).strip().lower()
    if not name or len(name) > 120:
        return jsonify({"error": "Group name must be between 1 and 120 characters."}), 400
    if not _SLUG_PATTERN.fullmatch(slug):
        return (
            jsonify({"error": "Group slug must contain lowercase letters, numbers, or hyphens."}),
            400,
        )
    if Group.query.filter_by(slug=slug).first():
        return jsonify({"error": "That group slug is already in use."}), 409

    group = Group(name=name, slug=slug, created_by_user_id=current_user.id)
    group.memberships.append(GroupMembership(user_id=current_user.id, role="owner"))
    db.session.add(group)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "That group slug is already in use."}), 409
    return jsonify({"group": {"id": group.id, "name": group.name, "slug": group.slug}}), 201


@bp.post("/api/groups/<group_id>/invitations")
@login_required
def create_group_invitation(group_id: str):
    """Create a one-time invitation for a group the user owns.

    Args:
        group_id: Group receiving the invitation.
    """
    membership = _owner_membership_or_404(group_id)
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip().lower() or None
    role = str(payload.get("role") or "member").strip().lower()
    if role not in {"owner", "member"}:
        return jsonify({"error": "Role must be owner or member."}), 400
    if email and (len(email) > 255 or "@" not in email):
        return jsonify({"error": "A valid invitation email is required."}), 400
    try:
        expires_in_days = int(payload.get("expires_in_days", 7))
    except (TypeError, ValueError):
        return jsonify({"error": "expires_in_days must be an integer."}), 400
    if expires_in_days < 1 or expires_in_days > 30:
        return jsonify({"error": "expires_in_days must be between 1 and 30."}), 400

    token = secrets.token_urlsafe(32)
    invitation = GroupInvitation(
        group_id=membership.group_id,
        token_hash=_hash_token(token),
        email=email,
        role=role,
        created_by_user_id=current_user.id,
        expires_at=utcnow() + timedelta(days=expires_in_days),
    )
    db.session.add(invitation)
    db.session.commit()
    return (
        jsonify(
            {
                "invitation": {
                    "id": invitation.id,
                    "group_id": invitation.group_id,
                    "email": invitation.email,
                    "role": invitation.role,
                    "expires_at": invitation.expires_at.isoformat(),
                    "token": token,
                    "join_url": url_for("tenancy.join_group", _external=True),
                }
            }
        ),
        201,
    )


@bp.get("/api/groups/<group_id>/members")
@login_required
def list_group_members(group_id: str):
    """Return members of a group visible to the current user.

    Args:
        group_id: Group whose members should be listed.
    """
    _group_membership_or_404(group_id)
    memberships = (
        GroupMembership.query.filter_by(group_id=group_id)
        .join(GroupMembership.user)
        .order_by(GroupMembership.created_at.asc())
        .all()
    )
    return jsonify(
        {
            "members": [
                {
                    "user_id": membership.user_id,
                    "email": membership.user.email,
                    "role": membership.role,
                }
                for membership in memberships
            ]
        }
    )


@bp.delete("/api/groups/<group_id>/members/<int:user_id>")
@login_required
def remove_group_member(group_id: str, user_id: int):
    """Remove a member while ensuring each group retains an owner.

    Args:
        group_id: Group from which to remove the user.
        user_id: User primary key to remove.
    """
    _owner_membership_or_404(group_id)
    db.session.execute(select(Group.id).where(Group.id == group_id).with_for_update())
    membership = GroupMembership.query.filter_by(
        group_id=group_id,
        user_id=user_id,
    ).first_or_404()
    if membership.role == "owner":
        owner_count = GroupMembership.query.filter_by(group_id=group_id, role="owner").count()
        if owner_count <= 1:
            return jsonify({"error": "A group must keep at least one owner."}), 409
    db.session.delete(membership)
    db.session.commit()
    return "", 204


@bp.post("/api/groups/join")
@login_required
def join_group():
    """Consume a valid invitation and create the current user's membership."""
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Invitation token is required."}), 400

    invitation = db.session.scalar(
        select(GroupInvitation)
        .where(GroupInvitation.token_hash == _hash_token(token))
        .with_for_update()
    )
    if invitation is None:
        return jsonify({"error": "Invitation is invalid."}), 404
    if invitation.accepted_at is not None:
        return jsonify({"error": "Invitation has already been used."}), 409
    if _is_expired(invitation.expires_at):
        return jsonify({"error": "Invitation has expired."}), 410
    if invitation.email and invitation.email.lower() != current_user.email.lower():
        return jsonify({"error": "Invitation was issued to a different email address."}), 403

    membership = GroupMembership.query.filter_by(
        group_id=invitation.group_id,
        user_id=current_user.id,
    ).first()
    if membership is None:
        membership = GroupMembership(
            group_id=invitation.group_id,
            user_id=current_user.id,
            role=invitation.role,
        )
        db.session.add(membership)
    invitation.accepted_at = utcnow()
    invitation.accepted_by_user_id = current_user.id
    db.session.commit()
    return jsonify(
        {
            "membership": {
                "group_id": membership.group_id,
                "group": membership.group.name,
                "role": membership.role,
            }
        }
    )


@bp.get("/api/me/mcp-namespaces")
@login_required
def list_my_mcp_namespaces():
    """Return the current user's effective MCP namespace union."""
    namespaces = accessible_mcp_namespaces(current_user.id)
    return jsonify(
        {
            "mcp_namespaces": [
                {
                    "namespace": item.namespace,
                    "display_name": item.display_name,
                    "description": item.description,
                }
                for item in namespaces
            ]
        }
    )


@bp.post("/api/admin/mcp-namespaces")
@platform_admin_required
def create_mcp_namespace():
    """Validate and register a remote MCP namespace."""
    try:
        payload = MCPNamespaceCreate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": _pydantic_error_message(exc)}), 400
    if MCPNamespace.query.filter_by(namespace=payload.namespace).first():
        return jsonify({"error": "That MCP namespace already exists."}), 409

    item = MCPNamespace(
        namespace=payload.namespace,
        display_name=payload.display_name,
        description=payload.description,
        transport=payload.transport,
        url=str(payload.url),
        auth_token_env_var=payload.auth_token_env_var,
        headers=payload.headers,
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "That MCP namespace already exists."}), 409
    response = MCPNamespaceEnvelope(mcp_namespace=_admin_namespace_model(item))
    return jsonify(response.model_dump(mode="json")), 201


@bp.put("/api/admin/groups/<group_id>/mcp-namespaces/<namespace>")
@platform_admin_required
def grant_group_mcp_namespace(group_id: str, namespace: str):
    """Grant a group access to an MCP namespace.

    Args:
        group_id: Group receiving the grant.
        namespace: Registered namespace name.
    """
    group = db.session.get(Group, group_id)
    item = MCPNamespace.query.filter_by(namespace=namespace.lower()).first()
    if group is None or item is None:
        abort(404)
    grant = GroupMCPNamespace.query.filter_by(
        group_id=group.id,
        mcp_namespace_id=item.id,
    ).first()
    if grant is None:
        db.session.add(GroupMCPNamespace(group_id=group.id, mcp_namespace_id=item.id))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    return jsonify({"group_id": group.id, "mcp_namespace": item.namespace})


@bp.delete("/api/admin/groups/<group_id>/mcp-namespaces/<namespace>")
@platform_admin_required
def revoke_group_mcp_namespace(group_id: str, namespace: str):
    """Revoke a group's access to an MCP namespace.

    Args:
        group_id: Group losing the grant.
        namespace: Registered namespace name.
    """
    item = MCPNamespace.query.filter_by(namespace=namespace.lower()).first()
    if item is None:
        abort(404)
    grant = GroupMCPNamespace.query.filter_by(
        group_id=group_id,
        mcp_namespace_id=item.id,
    ).first()
    if grant is None:
        abort(404)
    db.session.delete(grant)
    db.session.commit()
    return "", 204


def _owner_membership_or_404(group_id: str) -> GroupMembership:
    """Load the current user's owner membership or abort.

    Args:
        group_id: Group that must be owned.

    Returns:
        The current user's owner membership.
    """
    membership = _group_membership_or_404(group_id)
    if membership.role != "owner":
        abort(403)
    return membership


def _group_membership_or_404(group_id: str) -> GroupMembership:
    """Load the current user's membership in a group or raise a 404.

    Args:
        group_id: Group that must be visible to the current user.

    Returns:
        The current user's membership.
    """
    return GroupMembership.query.filter_by(
        group_id=group_id,
        user_id=current_user.id,
    ).first_or_404()


def _slugify(value: str) -> str:
    """Convert a group name into a bounded URL-safe slug.

    Args:
        value: Human-readable group name.

    Returns:
        A lowercase hyphen-separated slug.
    """
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:80]


def _hash_token(token: str) -> str:
    """Hash an invitation token for safe persistence.

    Args:
        token: Plain-text bearer token returned to the inviter.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_expired(expires_at) -> bool:
    """Compare an invitation expiration timestamp with current UTC time.

    Args:
        expires_at: Naive or timezone-aware expiration timestamp.

    Returns:
        Whether the timestamp has passed.
    """
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= utcnow()


def _admin_namespace_model(item: MCPNamespace) -> MCPNamespaceResponse:
    """Serialize a persisted namespace for the administrator API.

    Args:
        item: Namespace database model.

    Returns:
        Validated response model with grant count.
    """
    return MCPNamespaceResponse(
        namespace=item.namespace,
        display_name=item.display_name,
        description=item.description,
        transport=item.transport,
        url=item.url,
        auth_token_env_var=item.auth_token_env_var,
        headers=item.headers,
        enabled=item.enabled,
        group_grant_count=len(item.group_grants),
        created_at=item.created_at,
    )


def _pydantic_error_message(exc: ValidationError) -> str:
    """Flatten the first Pydantic error for a concise API response.

    Args:
        exc: Pydantic validation exception.

    Returns:
        Field-qualified validation message.
    """
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"])
    message = str(error["msg"])
    return f"{location}: {message}" if location else message
