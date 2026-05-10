from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
from pgvector.sqlalchemy import Vector
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .extensions import db
from .security import hash_password, password_needs_rehash, verify_password


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_settings() -> dict:
    return {
        "model_name": current_app.config.get("DEFAULT_MODEL", "gpt-5.5"),
        "reasoning_effort": current_app.config.get("DEFAULT_REASONING_EFFORT", "medium"),
        "reasoning_summaries_enabled": True,
        "memory_enabled": True,
        "markdown_options": {
            "gfm": True,
            "syntax_highlighting": True,
            "sanitize_html": True,
        },
        "theme": "system",
        "font_size": "medium",
        "compact_mode": False,
        "streaming_speed": "normal",
        "privacy": {
            "save_chat_history": True,
            "allow_memory_proposals": True,
        },
    }


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    session_version = db.Column(db.Integer, default=1, nullable=False)

    settings = db.relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    threads = db.relationship("ChatThread", back_populates="user", cascade="all, delete-orphan")
    conversation_memory_snapshots = db.relationship(
        "ConversationMemorySnapshot",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    conversation_memories = db.relationship(
        "ConversationMemory",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    long_term_memories = db.relationship(
        "LongTermMemory",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    message_telemetry = db.relationship(
        "MessageTelemetry",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = hash_password(password)

    def check_password(self, password: str) -> bool:
        return verify_password(self.password_hash, password)

    def password_needs_rehash(self) -> bool:
        return password_needs_rehash(self.password_hash)

    def make_token(self, purpose: str) -> str:
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        return serializer.dumps({"user_id": self.id, "purpose": purpose})

    @staticmethod
    def verify_token(token: str, purpose: str, max_age: int = 3600) -> "User | None":
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        try:
            data = serializer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        if data.get("purpose") != purpose:
            return None
        return db.session.get(User, data.get("user_id"))


class UserSettings(db.Model):
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    data = db.Column(db.JSON, nullable=False, default=default_settings)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="settings")

    def merged(self) -> dict:
        merged = default_settings()
        merged.update(self.data or {})
        merged["markdown_options"] = {
            **default_settings()["markdown_options"],
            **(self.data or {}).get("markdown_options", {}),
        }
        merged["privacy"] = {
            **default_settings()["privacy"],
            **(self.data or {}).get("privacy", {}),
        }
        return merged


class ChatThread(db.Model):
    __tablename__ = "chat_threads"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(180), nullable=False, default="New chat")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="threads")
    messages = db.relationship(
        "ChatMessage",
        back_populates="thread",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )
    memory_snapshots = db.relationship(
        "ConversationMemorySnapshot",
        back_populates="thread",
        order_by="ConversationMemorySnapshot.created_at",
        cascade="all, delete-orphan",
    )
    conversation_memories = db.relationship(
        "ConversationMemory",
        back_populates="thread",
        order_by="ConversationMemory.created_at",
        cascade="all, delete-orphan",
    )
    message_telemetry = db.relationship(
        "MessageTelemetry",
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    content = db.Column(db.Text, nullable=False)
    reasoning_summary = db.Column(db.Text, nullable=True)
    message_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    thread = db.relationship("ChatThread", back_populates="messages")
    telemetry = db.relationship(
        "MessageTelemetry",
        back_populates="message",
        uselist=False,
        cascade="all, delete-orphan",
    )
    conversation_memories = db.relationship(
        "ConversationMemory",
        foreign_keys="ConversationMemory.source_message_id",
        back_populates="source_message",
    )


class MessageTelemetry(db.Model):
    __tablename__ = "message_telemetry"
    __table_args__ = (
        db.Index("ix_message_telemetry_thread_role_created", "thread_id", "role", "created_at"),
        db.Index("ix_message_telemetry_first_token_at", "first_token_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer,
        db.ForeignKey("chat_messages.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    role = db.Column(db.String(32), nullable=False)
    request_received_at = db.Column(db.DateTime(timezone=True), nullable=False)
    message_persisted_at = db.Column(db.DateTime(timezone=True), nullable=False)
    generation_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    first_token_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    token_count = db.Column(db.Integer, nullable=False, default=0)
    telemetry_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    message = db.relationship("ChatMessage", back_populates="telemetry")
    user = db.relationship("User", back_populates="message_telemetry")
    thread = db.relationship("ChatThread", back_populates="message_telemetry")


class ConversationMemorySnapshot(db.Model):
    __tablename__ = "conversation_memory_snapshots"
    __table_args__ = (
        db.Index(
            "ix_conversation_memory_snapshots_thread_created",
            "thread_id",
            "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    message_start_id = db.Column(
        db.Integer, db.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    message_end_id = db.Column(
        db.Integer, db.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    summary = db.Column(db.Text, nullable=False)
    token_count = db.Column(db.Integer, nullable=True)
    summary_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user = db.relationship("User", back_populates="conversation_memory_snapshots")
    thread = db.relationship("ChatThread", back_populates="memory_snapshots")
    start_message = db.relationship("ChatMessage", foreign_keys=[message_start_id])
    end_message = db.relationship("ChatMessage", foreign_keys=[message_end_id])
    memories = db.relationship("ConversationMemory", back_populates="snapshot")


class ConversationMemory(db.Model):
    __tablename__ = "conversation_memories"
    __table_args__ = (
        db.CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_conversation_memories_importance",
        ),
        db.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_conversation_memories_confidence",
        ),
        db.CheckConstraint("recall_count >= 0", name="ck_conversation_memories_recall_count"),
        db.Index(
            "ix_conversation_memories_user_thread_status_created",
            "user_id",
            "thread_id",
            "status",
            "created_at",
        ),
        db.Index(
            "ix_conversation_memories_thread_type_status",
            "thread_id",
            "memory_type",
            "status",
        ),
        db.Index("ix_conversation_memories_expires_at", "expires_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id = db.Column(
        db.Integer, db.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id = db.Column(
        db.String(36),
        db.ForeignKey("conversation_memory_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    memory_type = db.Column(db.String(40), nullable=False, default="fact")
    subject = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="active")
    importance = db.Column(db.Float, nullable=False, default=0.5)
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    recall_count = db.Column(db.Integer, nullable=False, default=0)
    last_recalled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    memory_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="conversation_memories")
    thread = db.relationship("ChatThread", back_populates="conversation_memories")
    source_message = db.relationship(
        "ChatMessage",
        foreign_keys=[source_message_id],
        back_populates="conversation_memories",
    )
    snapshot = db.relationship("ConversationMemorySnapshot", back_populates="memories")


class PendingMemory(db.Model):
    __tablename__ = "pending_memories"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_thread_id = db.Column(db.String(36), db.ForeignKey("chat_threads.id"), nullable=True)
    source_message_id = db.Column(db.Integer, db.ForeignKey("chat_messages.id"), nullable=True)
    memory_text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False, default="preference")
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    status = db.Column(db.String(32), nullable=False, default="pending")
    approved_memory_key = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User")
    long_term_memory = db.relationship(
        "LongTermMemory",
        back_populates="source_proposal",
        uselist=False,
    )


class LongTermMemory(db.Model):
    __tablename__ = "long_term_memories"
    __table_args__ = (
        db.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_long_term_memories_confidence",
        ),
        db.Index("ix_long_term_memories_user_category", "user_id", "category"),
        db.Index("ix_long_term_memories_source_pending", "source_pending_memory_id"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_pending_memory_id = db.Column(
        db.String(36),
        db.ForeignKey("pending_memories.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False, default="preference")
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    embedding_model = db.Column(db.String(120), nullable=False)
    embedding = db.Column(Vector(1536), nullable=False)
    memory_metadata = db.Column(db.JSON, nullable=False, default=dict)
    last_retrieved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    retrieval_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="long_term_memories")
    source_proposal = db.relationship("PendingMemory", back_populates="long_term_memory")
