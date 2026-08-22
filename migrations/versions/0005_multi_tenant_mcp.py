"""Add multi-tenant groups and MCP namespace grants.

Revision ID: 0005_multi_tenant_mcp
Revises: 0004_pgvector_long_term_memory
Create Date: 2026-08-16 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_multi_tenant_mcp"
down_revision = "0004_pgvector_long_term_memory"
branch_labels = None
depends_on = None


def upgrade():
    """Add tenant groups, invitations, MCP namespaces, and access grants."""
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_groups_slug", "groups", ["slug"], unique=True)

    op.create_table(
        "mcp_namespaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="http"),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("auth_token_env_var", sa.String(length=255), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "transport IN ('http', 'streamable_http', 'sse')",
            name="ck_mcp_namespaces_transport",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mcp_namespaces_namespace",
        "mcp_namespaces",
        ["namespace"],
        unique=True,
    )

    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_group_memberships_role"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_group_memberships_group_user",
        ),
    )
    op.create_index(
        "ix_group_memberships_user_group",
        "group_memberships",
        ["user_id", "group_id"],
    )

    op.create_table(
        "group_mcp_namespaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("mcp_namespace_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["mcp_namespace_id"],
            ["mcp_namespaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "mcp_namespace_id",
            name="uq_group_mcp_namespaces_group_namespace",
        ),
    )
    op.create_index(
        "ix_group_mcp_namespaces_namespace_group",
        "group_mcp_namespaces",
        ["mcp_namespace_id", "group_id"],
    )

    op.create_table(
        "group_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("accepted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_group_invitations_role"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_group_invitations_group_created",
        "group_invitations",
        ["group_id", "created_at"],
    )
    op.create_index(
        "ix_group_invitations_token_hash",
        "group_invitations",
        ["token_hash"],
        unique=True,
    )


def downgrade():
    """Remove multi-tenant MCP access tables and administrator state."""
    op.drop_index("ix_group_invitations_token_hash", table_name="group_invitations")
    op.drop_index("ix_group_invitations_group_created", table_name="group_invitations")
    op.drop_table("group_invitations")
    op.drop_index(
        "ix_group_mcp_namespaces_namespace_group",
        table_name="group_mcp_namespaces",
    )
    op.drop_table("group_mcp_namespaces")
    op.drop_index("ix_group_memberships_user_group", table_name="group_memberships")
    op.drop_table("group_memberships")
    op.drop_index("ix_mcp_namespaces_namespace", table_name="mcp_namespaces")
    op.drop_table("mcp_namespaces")
    op.drop_index("ix_groups_slug", table_name="groups")
    op.drop_table("groups")
    op.drop_column("users", "is_platform_admin")
