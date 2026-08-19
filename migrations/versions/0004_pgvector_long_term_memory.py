"""Add pgvector long-term memory.

Revision ID: 0004_pgvector_long_term_memory
Revises: 0003_message_telemetry
Create Date: 2026-05-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004_pgvector_long_term_memory"
down_revision = "0003_message_telemetry"
branch_labels = None
depends_on = None


def upgrade():
    """Enable pgvector and add indexed, approved long-term memories."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_pending_memory_id", sa.String(length=36), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("memory_metadata", sa.JSON(), nullable=False),
        sa.Column("last_retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_long_term_memories_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["source_pending_memory_id"],
            ["pending_memories.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_pending_memory_id"),
    )
    op.create_index(
        "ix_long_term_memories_source_pending",
        "long_term_memories",
        ["source_pending_memory_id"],
    )
    op.create_index(
        "ix_long_term_memories_user_category",
        "long_term_memories",
        ["user_id", "category"],
    )
    op.execute(
        "CREATE INDEX ix_long_term_memories_embedding_hnsw "
        "ON long_term_memories USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade():
    """Remove long-term memory storage and the pgvector extension."""
    op.execute("DROP INDEX IF EXISTS ix_long_term_memories_embedding_hnsw")
    op.drop_index("ix_long_term_memories_user_category", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_source_pending", table_name="long_term_memories")
    op.drop_table("long_term_memories")
