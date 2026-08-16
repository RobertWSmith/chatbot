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

from .conftest import register


def test_user_gets_union_of_namespaces_across_multiple_groups(app):
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
    statement = mcp_access._accessible_mcp_namespaces_statement(1)

    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "EXISTS" in sql
    assert "SELECT DISTINCT" not in sql


def test_group_invitation_authenticates_membership_and_can_be_revoked(client, app):
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
        assert GroupMembership.query.filter_by(
            group_id=group_id,
            user_id=member_id,
        ).first() is None


def test_invitation_cannot_be_claimed_by_a_different_email(client):
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
    assert [item["namespace"] for item in visible.get_json()["mcp_namespaces"]] == [
        "billing"
    ]


def test_mcp_loader_isolates_namespace_failures_and_marks_tools(app, monkeypatch):
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
    user = User(email=email)
    user.set_password("very-secure-password")
    db.session.add(user)
    db.session.flush()
    return user


def _namespace(namespace: str, *, enabled: bool = True) -> MCPNamespace:
    return MCPNamespace(
        namespace=namespace,
        display_name=namespace.title(),
        url=f"https://{namespace}.example.com/mcp",
        enabled=enabled,
    )


def _login(client, email: str):
    return client.post(
        "/login",
        data={"email": email, "password": "very-secure-password"},
        follow_redirects=True,
    )
