from __future__ import annotations

import uuid
from datetime import UTC, datetime

from flask import current_app
from flask_login import UserMixin
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pgvector.sqlalchemy import Vector

from .extensions import db
from .security import hash_password, password_needs_rehash, verify_password

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, knowledgeable, and trustworthy AI assistant. Answer the "
    "user's questions directly and accurately. Be concise by default, but provide "
    "additional detail when it improves understanding. Use clear structure and "
    "practical examples when helpful. If a request is ambiguous, ask a focused "
    "clarifying question. If you are uncertain or lack enough information, say so "
    "rather than inventing facts. Maintain a friendly, professional tone and follow "
    "the user's requested style and format."
)


def utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def default_settings() -> dict:
    """Build a fresh settings dictionary from the application defaults.

    Returns:
        A complete, independently mutable user-settings dictionary.
    """
    return {
        "model_name": current_app.config.get("DEFAULT_MODEL", "gpt-5.5"),
        "reasoning_effort": current_app.config.get("DEFAULT_REASONING_EFFORT", "medium"),
        "reasoning_provider": "openai",
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
    """Represent an authenticated application user."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_platform_admin = db.Column(db.Boolean, default=False, nullable=False)
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
    tool_call_telemetry = db.relationship(
        "ToolCallTelemetry",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    group_memberships = db.relationship(
        "GroupMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        """Hash and store a replacement password.

        Args:
            password: Plain-text password to store securely.
        """
        self.password_hash = hash_password(password)

    def check_password(self, password: str) -> bool:
        """Check a candidate password against the stored hash.

        Args:
            password: Candidate plain-text password.

        Returns:
            Whether the password matches.
        """
        return verify_password(self.password_hash, password)

    def password_needs_rehash(self) -> bool:
        """Return whether the stored password hash should be upgraded."""
        return password_needs_rehash(self.password_hash)

    def make_token(self, purpose: str) -> str:
        """Create a signed token scoped to this user and a purpose.

        Args:
            purpose: Action for which the token will be accepted.

        Returns:
            A signed token string.
        """
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        return serializer.dumps({"user_id": self.id, "purpose": purpose})

    @staticmethod
    def verify_token(token: str, purpose: str, max_age: int = 3600) -> User | None:
        """Validate a signed user token.

        Args:
            token: Serialized token to validate.
            purpose: Required token purpose.
            max_age: Maximum token age in seconds.

        Returns:
            The token's user when valid, or ``None`` otherwise.
        """
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        try:
            data = serializer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        if data.get("purpose") != purpose:
            return None
        return db.session.get(User, data.get("user_id"))


class Group(db.Model):
    """Represent a tenant group that owns memberships and MCP grants."""

    __tablename__ = "groups"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    memberships = db.relationship(
        "GroupMembership",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    namespace_grants = db.relationship(
        "GroupMCPNamespace",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    invitations = db.relationship(
        "GroupInvitation",
        back_populates="group",
        cascade="all, delete-orphan",
    )


class GroupMembership(db.Model):
    """Associate a user with a group and an authorization role."""

    __tablename__ = "group_memberships"
    __table_args__ = (
        db.UniqueConstraint("group_id", "user_id", name="uq_group_memberships_group_user"),
        db.CheckConstraint("role IN ('owner', 'member')", name="ck_group_memberships_role"),
        db.Index("ix_group_memberships_user_group", "user_id", "group_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(
        db.String(36),
        db.ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = db.Column(db.String(20), nullable=False, default="member")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    group = db.relationship("Group", back_populates="memberships")
    user = db.relationship("User", back_populates="group_memberships")


class MCPNamespace(db.Model):
    """Describe a remotely hosted MCP server namespace."""

    __tablename__ = "mcp_namespaces"
    __table_args__ = (
        db.CheckConstraint(
            "transport IN ('http', 'streamable_http', 'sse')",
            name="ck_mcp_namespaces_transport",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    namespace = db.Column(db.String(63), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    transport = db.Column(db.String(32), nullable=False, default="http")
    url = db.Column(db.String(2048), nullable=False)
    auth_token_env_var = db.Column(db.String(255), nullable=True)
    headers = db.Column(db.JSON, nullable=False, default=dict)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    group_grants = db.relationship(
        "GroupMCPNamespace",
        back_populates="mcp_namespace",
        cascade="all, delete-orphan",
    )


class GroupMCPNamespace(db.Model):
    """Grant a group access to an MCP namespace."""

    __tablename__ = "group_mcp_namespaces"
    __table_args__ = (
        db.UniqueConstraint(
            "group_id",
            "mcp_namespace_id",
            name="uq_group_mcp_namespaces_group_namespace",
        ),
        db.Index("ix_group_mcp_namespaces_namespace_group", "mcp_namespace_id", "group_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(
        db.String(36),
        db.ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_namespace_id = db.Column(
        db.Integer,
        db.ForeignKey("mcp_namespaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    group = db.relationship("Group", back_populates="namespace_grants")
    mcp_namespace = db.relationship("MCPNamespace", back_populates="group_grants")


class GroupInvitation(db.Model):
    """Represent a one-time, optionally email-bound group invitation."""

    __tablename__ = "group_invitations"
    __table_args__ = (
        db.CheckConstraint("role IN ('owner', 'member')", name="ck_group_invitations_role"),
        db.Index("ix_group_invitations_group_created", "group_id", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(
        db.String(36),
        db.ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="member")
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    accepted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    group = db.relationship("Group", back_populates="invitations")
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    accepted_by = db.relationship("User", foreign_keys=[accepted_by_user_id])


class UserSettings(db.Model):
    """Store per-user overrides for application settings."""

    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    system_prompt = db.Column(db.Text, nullable=False, default=DEFAULT_SYSTEM_PROMPT)
    data = db.Column(db.JSON, nullable=False, default=default_settings)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="settings")

    def merged(self) -> dict:
        """Merge stored overrides into a fresh set of defaults.

        Returns:
            Complete effective settings without shared nested mappings.
        """
        defaults = default_settings()
        overrides = self.data or {}
        merged = {**defaults, **overrides}
        for nested_key in ("markdown_options", "privacy"):
            merged[nested_key] = {
                **defaults[nested_key],
                **overrides.get(nested_key, {}),
            }
        if merged.get("reasoning_effort") == "minimal":
            merged["reasoning_effort"] = "none"
        merged["system_prompt"] = self.system_prompt or DEFAULT_SYSTEM_PROMPT
        return merged


class ChatThread(db.Model):
    """Represent one user-owned chat conversation."""

    __tablename__ = "chat_threads"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(180), nullable=False, default="New chat")
    model_name = db.Column(db.String(80), nullable=True)
    reasoning_effort = db.Column(db.String(32), nullable=True)
    reasoning_provider = db.Column(db.String(32), nullable=True)
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
    tool_call_telemetry = db.relationship(
        "ToolCallTelemetry",
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class ChatMessage(db.Model):
    """Represent a user or assistant message in a chat thread."""

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
    tool_calls = db.relationship(
        "ToolCallTelemetry",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="ToolCallTelemetry.started_at",
    )
    conversation_memories = db.relationship(
        "ConversationMemory",
        foreign_keys="ConversationMemory.source_message_id",
        back_populates="source_message",
    )


class MessageTelemetry(db.Model):
    """Capture lifecycle timing and token metrics for one chat message."""

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
    model_name = db.Column(db.String(80), nullable=True)
    reasoning_effort = db.Column(db.String(32), nullable=True)
    telemetry_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    message = db.relationship("ChatMessage", back_populates="telemetry")
    user = db.relationship("User", back_populates="message_telemetry")
    thread = db.relationship("ChatThread", back_populates="message_telemetry")


class ToolCallTelemetry(db.Model):
    """Record one privacy-conscious tool invocation for an assistant turn."""

    __tablename__ = "tool_call_telemetry"
    __table_args__ = (
        db.Index("ix_tool_call_telemetry_thread_started", "thread_id", "started_at"),
        db.Index("ix_tool_call_telemetry_message_started", "message_id", "started_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer,
        db.ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    run_id = db.Column(db.String(36), unique=True, nullable=False)
    parent_run_id = db.Column(db.String(36), nullable=True)
    tool_name = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(255), nullable=False)
    input_summary = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(32), nullable=False)
    duration_ms = db.Column(db.Integer, nullable=True)
    output_type = db.Column(db.String(120), nullable=True)
    output_chars = db.Column(db.Integer, nullable=True)
    error_type = db.Column(db.String(255), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    message = db.relationship("ChatMessage", back_populates="tool_calls")
    user = db.relationship("User", back_populates="tool_call_telemetry")
    thread = db.relationship("ChatThread", back_populates="tool_call_telemetry")


class ConversationMemorySnapshot(db.Model):
    """Store a rolling summary of older messages in a chat thread."""

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
    """Store structured short-term memory extracted from a conversation."""

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
    """Represent a long-term memory proposal awaiting user review."""

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
    """Store an approved memory and its retrieval embedding."""

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
