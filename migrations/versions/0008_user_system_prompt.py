"""Add a database-backed user system prompt.

Revision ID: 0008_user_system_prompt
Revises: 0007_reasoning_provider
Create Date: 2026-08-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_user_system_prompt"
down_revision = "0007_reasoning_provider"
branch_labels = None
depends_on = None

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, knowledgeable, and trustworthy AI assistant. Answer the "
    "user's questions directly and accurately. Be concise by default, but provide "
    "additional detail when it improves understanding. Use clear structure and "
    "practical examples when helpful. If a request is ambiguous, ask a focused "
    "clarifying question. If you are uncertain or lack enough information, say so "
    "rather than inventing facts. Maintain a friendly, professional tone and follow "
    "the user's requested style and format."
)


def upgrade():
    """Store a customizable prompt for existing and future users."""
    op.add_column(
        "user_settings",
        sa.Column(
            "system_prompt",
            sa.Text(),
            nullable=False,
            server_default=DEFAULT_SYSTEM_PROMPT,
        ),
    )


def downgrade():
    """Remove the user system prompt."""
    op.drop_column("user_settings", "system_prompt")
