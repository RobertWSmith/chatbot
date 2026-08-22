"""Add a thread-scoped reasoning provider.

Revision ID: 0005_reasoning_provider
Revises: 0004_pgvector_long_term_memory
Create Date: 2026-08-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_reasoning_provider"
down_revision = "0004_pgvector_long_term_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_threads",
        sa.Column("reasoning_provider", sa.String(length=32), nullable=True),
    )


def downgrade():
    op.drop_column("chat_threads", "reasoning_provider")
