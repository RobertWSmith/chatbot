"""Add per-chat model and reasoning settings.

Revision ID: 0006_chat_generation_settings
Revises: 0005_multi_tenant_mcp
Create Date: 2026-08-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_chat_generation_settings"
down_revision = "0005_multi_tenant_mcp"
branch_labels = None
depends_on = None


def upgrade():
    """Store generation choices per chat and per turn, plus tool calls."""
    op.add_column("chat_threads", sa.Column("model_name", sa.String(length=80), nullable=True))
    op.add_column(
        "chat_threads",
        sa.Column("reasoning_effort", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "message_telemetry",
        sa.Column("model_name", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "message_telemetry",
        sa.Column("reasoning_effort", sa.String(length=32), nullable=True),
    )
    op.create_table(
        "tool_call_telemetry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_type", sa.String(length=120), nullable=True),
        sa.Column("output_chars", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_tool_call_telemetry_message_started",
        "tool_call_telemetry",
        ["message_id", "started_at"],
    )
    op.create_index(
        "ix_tool_call_telemetry_thread_started",
        "tool_call_telemetry",
        ["thread_id", "started_at"],
    )


def downgrade():
    """Remove tool-call telemetry and generation choices."""
    op.drop_index("ix_tool_call_telemetry_thread_started", table_name="tool_call_telemetry")
    op.drop_index("ix_tool_call_telemetry_message_started", table_name="tool_call_telemetry")
    op.drop_table("tool_call_telemetry")
    op.drop_column("message_telemetry", "reasoning_effort")
    op.drop_column("message_telemetry", "model_name")
    op.drop_column("chat_threads", "reasoning_effort")
    op.drop_column("chat_threads", "model_name")
