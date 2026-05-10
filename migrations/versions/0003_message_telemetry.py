"""add message telemetry

Revision ID: 0003_message_telemetry
Revises: 0002_conversation_memory
Create Date: 2026-05-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_message_telemetry"
down_revision = "0002_conversation_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "message_telemetry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("request_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_token_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("telemetry_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        "ix_message_telemetry_first_token_at",
        "message_telemetry",
        ["first_token_at"],
    )
    op.create_index(
        "ix_message_telemetry_thread_role_created",
        "message_telemetry",
        ["thread_id", "role", "created_at"],
    )


def downgrade():
    op.drop_index("ix_message_telemetry_thread_role_created", table_name="message_telemetry")
    op.drop_index("ix_message_telemetry_first_token_at", table_name="message_telemetry")
    op.drop_table("message_telemetry")
