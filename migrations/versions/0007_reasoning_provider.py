"""Add a thread-scoped reasoning provider.

Revision ID: 0007_reasoning_provider
Revises: 0006_chat_generation_settings
Create Date: 2026-08-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_reasoning_provider"
down_revision = "0006_chat_generation_settings"
branch_labels = None
depends_on = None


def upgrade():
    """Store the selected reasoning provider on each chat thread."""
    op.add_column(
        "chat_threads",
        sa.Column("reasoning_provider", sa.String(length=32), nullable=True),
    )


def downgrade():
    """Remove the thread-scoped reasoning provider."""
    op.drop_column("chat_threads", "reasoning_provider")
