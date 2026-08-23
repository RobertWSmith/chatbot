import asyncio
from types import SimpleNamespace

from app.extensions import db
from app.models import Group, GroupMCPNamespace, GroupMembership, MCPNamespace, User
from app.services import mcp_access
from app.services.mcp_access import accessible_mcp_namespaces
from app.tenancy import routes as tenancy_routes

from .conftest import register


def test_admin_page_documents_container_and_host_endpoints(client, app):
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")

    response = client.get("/admin/mcp")

    assert response.status_code == 200
    assert b"http://mcp-portal:8001/mcp" in response.data
    assert b"http://localhost:8001/mcp" in response.data
    assert b"Configure portal for me" in response.data
    with app.app_context():
        assert (
            User.query.filter_by(email="admin@example.com").one().is_platform_admin
            is True
        )


def test_non_admin_gets_actionable_admin_page(client):
    register(client)

    response = client.get("/admin/mcp")

    assert response.status_code == 403
    assert b"PLATFORM_ADMIN_EMAILS" in response.data


def test_portal_bootstrap_registers_and_grants_namespace(client, app):
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
        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == [
            "portal"
        ]
        assert MCPNamespace.query.filter_by(namespace="portal").count() == 1
        assert GroupMembership.query.filter_by(user_id=user.id).count() == 1
        assert GroupMCPNamespace.query.count() == 1


def test_portal_bootstrap_consolidates_legacy_portal_alias(client, app):
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
        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == [
            "portal"
        ]
        assert MCPNamespace.query.filter_by(namespace="portalv2").one().enabled is False
        assert Group.query.count() == 1


def test_namespace_probe_returns_discovered_tools(client, app, monkeypatch):
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")
    client.post("/api/admin/mcp-portal/bootstrap")

    async def fake_probe(item):
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
    app.config["PLATFORM_ADMIN_EMAILS"] = ("admin@example.com",)
    register(client, email="admin@example.com")
    client.post("/api/admin/mcp-portal/bootstrap")

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"MCP Portal" in response.data
    assert b"http://mcp-portal:8001/mcp" in response.data
    assert b"Configure MCP" in response.data


def test_namespace_union_is_deduplicated_across_groups(app):
    with app.app_context():
        user = _user("member@example.com")
        portal = _namespace("portal")
        first = Group(name="First", slug="first", created_by_user_id=user.id)
        second = Group(name="Second", slug="second", created_by_user_id=user.id)
        first.memberships.append(GroupMembership(user=user, role="owner"))
        second.memberships.append(GroupMembership(user=user, role="member"))
        first.namespace_grants.append(GroupMCPNamespace(mcp_namespace=portal))
        second.namespace_grants.append(GroupMCPNamespace(mcp_namespace=portal))
        db.session.add_all([first, second])
        db.session.commit()

        assert [item.namespace for item in accessible_mcp_namespaces(user.id)] == [
            "portal"
        ]


def test_mcp_loader_isolates_namespace_failures(app, monkeypatch):
    with app.app_context():
        user = _user("tools@example.com")
        group = Group(name="Tools", slug="tools", created_by_user_id=user.id)
        group.memberships.append(GroupMembership(user=user, role="owner"))
        portal = _namespace("portal")
        broken = _namespace("broken")
        group.namespace_grants.extend(
            [
                GroupMCPNamespace(mcp_namespace=portal),
                GroupMCPNamespace(mcp_namespace=broken),
            ]
        )
        db.session.add(group)
        db.session.commit()

        async def fake_load(item):
            if item.namespace == "broken":
                raise ConnectionError("private server detail")
            return [
                SimpleNamespace(
                    name="mcp_portal_search", description="Search", metadata={}
                )
            ]

        monkeypatch.setattr(mcp_access, "_load_namespace_tools", fake_load)
        result = asyncio.run(mcp_access.load_authorized_mcp_tools(user.id))

    assert result.loaded_namespaces == ("portal",)
    assert result.unavailable_namespaces == ("broken",)
    assert [tool.name for tool in result.tools] == ["mcp_portal_search"]


def _user(email: str) -> User:
    user = User(email=email)
    user.set_password("very-secure-password")
    db.session.add(user)
    db.session.flush()
    return user


def _namespace(namespace: str) -> MCPNamespace:
    return MCPNamespace(
        namespace=namespace,
        display_name=namespace.title(),
        url=f"https://{namespace}.example.com/mcp",
    )
