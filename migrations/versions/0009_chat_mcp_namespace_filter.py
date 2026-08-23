"""Add a thread-scoped MCP namespace filter.

Revision ID: 0009_chat_mcp_namespace_filter
Revises: 0008_user_system_prompt
Create Date: 2026-08-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_chat_mcp_namespace_filter"
down_revision = "0008_user_system_prompt"
branch_labels = None
depends_on = None


def upgrade():
    """Store an optional MCP namespace filter on each chat thread."""
    op.add_column(
        "chat_threads",
        sa.Column("mcp_namespace_filter", sa.JSON(), nullable=True),
    )


def downgrade():
    """Remove the thread-scoped MCP namespace filter."""
    op.drop_column("chat_threads", "mcp_namespace_filter")
