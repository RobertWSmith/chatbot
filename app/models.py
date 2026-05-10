from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
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
