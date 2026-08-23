import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.extensions import db
from app.models import (
    Group,
    GroupMCPNamespace,
    GroupMembership,
    MCPNamespace,
    User,
)
from app.services import mcp_access
from app.services.mcp_access import accessible_mcp_namespaces
from app.tenancy import routes as tenancy_routes

from .conftest import register


def test_user_gets_union_of_namespaces_across_multiple_groups(app):
    """Ensure effective MCP access unions enabled grants across groups."""
    with app.app_context():
        user = _user("member@example.com")
        first = Group(name="First", slug="first", created_by_user_id=user.id)
        second = Group(name="Second", slug="second", created_by_user_id=user.id)
        first.memberships.append(GroupMembership(user=user, role="owner"))
        second.memberships.append(GroupMembership(user=user, role="member"))
        alpha = _namespace("alpha")
        beta = _namespace("beta")
        disabled = _namespace("disabled", enabled=False)
        first.namespace_grants.extend(
            [
                GroupMCPNamespace(mcp_namespace=alpha),
                GroupMCPNamespace(mcp_namespace=disabled),
            ]
        )
        second.namespace_grants.extend(
            [
                GroupMCPNamespace(mcp_namespace=alpha),
                GroupMCPNamespace(mcp_namespace=beta),
            ]
        )
        db.session.add_all([first, second])
        db.session.commit()

        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == [
            "alpha",
            "beta",
        ]


def test_namespace_authorization_query_does_not_compare_postgres_json():
    """Ensure namespace authorization uses an existence query, not JSON distinctness."""
    statement = mcp_access._accessible_mcp_namespaces_statement(1)

    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "EXISTS" in sql
    assert "SELECT DISTINCT" not in sql


def test_group_invitation_authenticates_membership_and_can_be_revoked(client, app):
    """Ensure invitation membership grants access that owners can revoke."""
    register(client, email="owner@example.com")
    created = client.post("/api/groups", json={"name": "Engineering"})
    group_id = created.get_json()["group"]["id"]
    invited = client.post(
        f"/api/groups/{group_id}/invitations",
        json={"email": "member@example.com"},
    )
    token = invited.get_json()["invitation"]["token"]

    client.get("/logout")
    register(client, email="member@example.com")
    joined = client.post("/api/groups/join", json={"token": token})

    assert joined.status_code == 200
    assert joined.get_json()["membership"]["group_id"] == group_id
    assert client.post("/api/groups/join", json={"token": token}).status_code == 409
    with app.app_context():
        member_id = User.query.filter_by(email="member@example.com").one().id

    client.get("/logout")
    _login(client, "owner@example.com")
    removed = client.delete(f"/api/groups/{group_id}/members/{member_id}")

    assert removed.status_code == 204
    with app.app_context():
        assert (
            GroupMembership.query.filter_by(
                group_id=group_id,
                user_id=member_id,
            ).first()
            is None
        )


def test_invitation_cannot_be_claimed_by_a_different_email(client):
    """Ensure an email-bound invitation rejects a different account."""
    register(client, email="owner@example.com")
    group_id = client.post("/api/groups", json={"name": "Private"}).get_json()["group"]["id"]
    token = client.post(
        f"/api/groups/{group_id}/invitations",
        json={"email": "expected@example.com"},
    ).get_json()["invitation"]["token"]
    client.get("/logout")
    register(client, email="other@example.com")

    response = client.post("/api/groups/join", json={"token": token})

    assert response.status_code == 403


def test_only_platform_admin_can_create_and_grant_namespace(client, app):
    """Ensure only platform administrators manage namespace registrations and grants."""
    register(client, email="admin@example.com")
    group_id = client.post("/api/groups", json={"name": "Operations"}).get_json()["group"]["id"]
    payload = {
        "namespace": "billing",
        "display_name": "Billing",
        "url": "https://mcp.example.com/tools",
        "auth_token_env_var": "BILLING_MCP_TOKEN",
    }

    assert client.post("/api/admin/mcp-namespaces", json=payload).status_code == 403
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        user.is_platform_admin = True
        db.session.commit()
    client.get("/logout")
    _login(client, "admin@example.com")

    created = client.post("/api/admin/mcp-namespaces", json=payload)
    granted = client.put(f"/api/admin/groups/{group_id}/mcp-namespaces/billing")
    visible = client.get("/api/me/mcp-namespaces")

    assert created.status_code == 201
    assert created.get_json()["mcp_namespace"]["auth_token_env_var"] == "BILLING_MCP_TOKEN"
    assert granted.status_code == 200
    assert [item["namespace"] for item in visible.get_json()["mcp_namespaces"]] == ["billing"]


def test_platform_admin_can_open_mcp_admin_and_list_namespaces(client, app):
    """Ensure administrators can view the MCP page and inventory API."""
    register(client, email="admin@example.com")
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        user.is_platform_admin = True
        db.session.add(_namespace("billing"))
        db.session.commit()
    client.get("/logout")
    _login(client, "admin@example.com")

    page = client.get("/admin/mcp")
    response = client.get("/api/admin/mcp-namespaces")

    assert page.status_code == 200
    assert b"MCP servers" in page.data
    assert b"mcp_billing" in page.data
    assert b'<script type="module" src="/static/js/admin-mcp.js"></script>' in page.data
    assert response.status_code == 200
    assert response.get_json()["mcp_namespaces"][0]["namespace"] == "billing"


def test_mcp_admin_page_renders_group_grant_management(client, app):
    """Ensure the admin screen can create and revoke grants for every tenant group."""
    register(client, email="admin@example.com")
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        user.is_platform_admin = True
        namespace = _namespace("portal")
        group = Group(name="Operations", slug="operations", created_by_user_id=None)
        group.namespace_grants.append(GroupMCPNamespace(mcp_namespace=namespace))
        db.session.add(group)
        db.session.commit()
        group_id = group.id
    client.get("/logout")
    _login(client, "admin@example.com")

    page = client.get("/admin/mcp")

    assert page.status_code == 200
    assert b'id="mcp-grant-form"' in page.data
    assert b'id="mcp-group-form"' in page.data
    assert b"Create tenant group" in page.data
    assert b'name="namespace"' in page.data
    assert b'value="portal"' in page.data
    assert b'name="group_id"' in page.data
    assert f'value="{group_id}"'.encode() in page.data
    assert b"Operations" in page.data
    assert b"data-revoke-grant" in page.data
    assert b"Access is recalculated" in page.data
    assert b"http://mcp-portal:8001/mcp" in page.data
    assert b"http://localhost:8001/mcp" in page.data
    assert b"Configure portal for me" in page.data


def test_portal_bootstrap_is_idempotent_and_grants_access(client, app):
    """Ensure quick setup creates one portal, membership, and namespace grant."""
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")

    first = client.post("/api/admin/mcp-portal/bootstrap")
    second = client.post("/api/admin/mcp-portal/bootstrap")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["mcp_namespace"]["url"] == "http://mcp-portal:8001/mcp"
    assert first.get_json()["mcp_namespace"]["transport"] == "streamable_http"
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == ["portal"]
        assert MCPNamespace.query.filter_by(namespace="portal").count() == 1
        assert GroupMembership.query.filter_by(user_id=user.id).count() == 1
        assert GroupMCPNamespace.query.count() == 1


def test_portal_bootstrap_consolidates_legacy_portal_alias(client, app):
    """Ensure quick setup disables duplicate portal records without losing grants."""
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        group = Group(name="Existing", slug="existing", created_by_user_id=user.id)
        group.memberships.append(GroupMembership(user=user, role="owner"))
        legacy = MCPNamespace(
            namespace="portalv2",
            display_name="Portal workaround",
            transport="streamable_http",
            url="http://mcp-portal:8001/mcp",
        )
        group.namespace_grants.append(GroupMCPNamespace(mcp_namespace=legacy))
        db.session.add(group)
        db.session.commit()

    response = client.post("/api/admin/mcp-portal/bootstrap")

    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == ["portal"]
        assert MCPNamespace.query.filter_by(namespace="portalv2").one().enabled is False
        assert Group.query.count() == 1


def test_namespace_probe_returns_discovered_tools(client, app, monkeypatch):
    """Ensure administrators can verify a configured namespace's tool inventory."""
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")
    client.post("/api/admin/mcp-portal/bootstrap")

    async def fake_probe(item):
        """Return a deterministic tool inventory for the portal namespace."""
        assert item.url == "http://mcp-portal:8001/mcp"
        return ["mcp_portal_public_duckduckgo_search"]

    monkeypatch.setattr(tenancy_routes, "probe_mcp_namespace", fake_probe)
    response = client.post("/api/admin/mcp-namespaces/portal/test")

    assert response.status_code == 200
    assert response.get_json() == {
        "namespace": "portal",
        "tool_count": 1,
        "tools": ["mcp_portal_public_duckduckgo_search"],
    }


def test_settings_page_shows_effective_mcp_access(client, app):
    """Ensure users can see their effective MCP access from account settings."""
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")
    client.post("/api/admin/mcp-portal/bootstrap")

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"MCP Portal" in response.data
    assert b"http://mcp-portal:8001/mcp" in response.data
    assert b"Configure MCP" in response.data


def test_mcp_admin_page_explains_access_to_non_admin_users(client):
    """Ensure non-administrators receive a helpful access-denied page."""
    register(client)

    page = client.get("/admin/mcp")
    api_response = client.get("/api/admin/mcp-namespaces")

    assert page.status_code == 403
    assert b"Platform admin access required" in page.data
    assert b"user@example.com" in page.data
    assert b"Sign out and use another account" in page.data
    assert api_response.status_code == 403
    assert api_response.get_json() == {"error": "Platform administrator access is required."}


def test_configured_existing_user_is_promoted_on_admin_request(client, app):
    """Ensure configured administrator email changes apply to existing users."""
    app.config["PLATFORM_ADMIN_EMAILS"] = ()
    register(client, email="configured-admin@example.com")
    with app.app_context():
        user = User.query.filter_by(email="configured-admin@example.com").one()
        assert user.is_platform_admin is False

    app.config["PLATFORM_ADMIN_EMAILS"] = ("configured-admin@example.com",)
    response = client.get("/admin/mcp")

    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="configured-admin@example.com").one()
        assert user.is_platform_admin is True


def test_anonymous_admin_request_redirects_to_login(client):
    """Ensure anonymous administrator requests require authentication."""
    response = client.get("/admin/mcp")

    assert response.status_code == 302
    assert "/login?next=%2Fadmin%2Fmcp" in response.headers["Location"]


def test_mcp_admin_create_uses_pydantic_for_input_and_output(client, app):
    """Ensure MCP creation validates input and serializes typed output."""
    register(client, email="admin@example.com")
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        user.is_platform_admin = True
        db.session.commit()
    client.get("/logout")
    _login(client, "admin@example.com")

    invalid = client.post(
        "/api/admin/mcp-namespaces",
        json={
            "namespace": "billing",
            "url": "https://mcp.example.com/tools",
            "headers": {"Authorization": "not-allowed"},
        },
    )
    created = client.post(
        "/api/admin/mcp-namespaces",
        json={
            "namespace": "Billing",
            "display_name": "Billing",
            "url": "https://mcp.example.com/tools",
        },
    )

    assert invalid.status_code == 400
    assert "auth_token_env_var" in invalid.get_json()["error"]
    assert created.status_code == 201
    payload = created.get_json()["mcp_namespace"]
    assert payload["namespace"] == "billing"
    assert payload["group_grant_count"] == 0
    assert payload["created_at"]


def test_mcp_loader_isolates_namespace_failures_and_marks_tools(app, monkeypatch):
    """Ensure one MCP failure is isolated and loaded tools retain provenance."""
    with app.app_context():
        user = _user("tools@example.com")
        group = Group(name="Tools", slug="tools", created_by_user_id=user.id)
        group.memberships.append(GroupMembership(user=user, role="owner"))
        alpha = _namespace("alpha")
        beta = _namespace("beta")
        group.namespace_grants.extend(
            [
                GroupMCPNamespace(mcp_namespace=alpha),
                GroupMCPNamespace(mcp_namespace=beta),
            ]
        )
        db.session.add(group)
        db.session.commit()

        async def fake_load(item):
            """Return one fake tool while simulating a namespace outage."""
            if item.namespace == "beta":
                raise ConnectionError("secret server detail")
            return [
                SimpleNamespace(
                    name="mcp_alpha_search",
                    description="Search records.",
                    metadata=None,
                )
            ]

        monkeypatch.setattr(mcp_access, "_load_namespace_tools", fake_load)
        result = asyncio.run(mcp_access.load_authorized_mcp_tools(user.id))

    assert result.loaded_namespaces == ("alpha",)
    assert result.unavailable_namespaces == ("beta",)
    assert [tool.name for tool in result.tools] == ["mcp_alpha_search"]
    assert result.tools[0].metadata["mcp_namespace"] == "alpha"
    assert result.tools[0].description.startswith("[MCP namespace: alpha]")


def test_mcp_connection_reads_bearer_token_from_environment(monkeypatch):
    """Ensure MCP bearer tokens are resolved from the configured environment variable."""
    item = SimpleNamespace(
        url="https://mcp.example.com/tools",
        transport="http",
        headers={"X-Tenant": "shared"},
        auth_token_env_var="EXAMPLE_MCP_TOKEN",
    )
    monkeypatch.setenv("EXAMPLE_MCP_TOKEN", "top-secret")

    config = mcp_access._connection_config(item)

    assert config["headers"] == {
        "X-Tenant": "shared",
        "Authorization": "Bearer top-secret",
    }


def test_mcp_connection_fails_closed_when_token_is_missing(monkeypatch):
    """Ensure missing MCP bearer tokens prevent connection configuration."""
    item = SimpleNamespace(
        url="https://mcp.example.com/tools",
        transport="http",
        headers={},
        auth_token_env_var="MISSING_MCP_TOKEN",
    )
    monkeypatch.delenv("MISSING_MCP_TOKEN", raising=False)

    with pytest.raises(mcp_access.MCPConfigurationError):
        mcp_access._connection_config(item)


def _user(email: str) -> User:
    """Build and stage a test user with valid credentials."""
    user = User(email=email)
    user.set_password("very-secure-password")
    db.session.add(user)
    db.session.flush()
    return user


def _namespace(namespace: str, *, enabled: bool = True) -> MCPNamespace:
    """Build a test MCP namespace with a public URL."""
    return MCPNamespace(
        namespace=namespace,
        display_name=namespace.title(),
        url=f"https://{namespace}.example.com/mcp",
        enabled=enabled,
    )


def _login(client, email: str):
    """Sign a test user in through the public login form."""
    return client.post(
        "/login",
        data={"email": email, "password": "very-secure-password"},
        follow_redirects=True,
    )
