"""add conversation memory schema

Revision ID: 0002_conversation_memory
Revises: 0001_initial
Create Date: 2026-05-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_conversation_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_memory_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("message_start_id", sa.Integer(), nullable=True),
        sa.Column("message_end_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("summary_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_end_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_start_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_memory_snapshots_thread_created",
        "conversation_memory_snapshots",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("memory_type", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("recall_count", sa.Integer(), nullable=False),
        sa.Column("last_recalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("memory_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_conversation_memories_confidence",
        ),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_conversation_memories_importance",
        ),
        sa.CheckConstraint("recall_count >= 0", name="ck_conversation_memories_recall_count"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["conversation_memory_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["source_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_memories_expires_at",
        "conversation_memories",
        ["expires_at"],
    )
    op.create_index(
        "ix_conversation_memories_thread_type_status",
        "conversation_memories",
        ["thread_id", "memory_type", "status"],
    )
    op.create_index(
        "ix_conversation_memories_user_thread_status_created",
        "conversation_memories",
        ["user_id", "thread_id", "status", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_conversation_memories_user_thread_status_created",
        table_name="conversation_memories",
    )
    op.drop_index(
        "ix_conversation_memories_thread_type_status",
        table_name="conversation_memories",
    )
    op.drop_index("ix_conversation_memories_expires_at", table_name="conversation_memories")
    op.drop_table("conversation_memories")
    op.drop_index(
        "ix_conversation_memory_snapshots_thread_created",
        table_name="conversation_memory_snapshots",
    )
    op.drop_table("conversation_memory_snapshots")
