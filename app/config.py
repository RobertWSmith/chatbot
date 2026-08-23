from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def env_flag(name: str, default: str = "0") -> bool:
    """Read a conventional boolean value from the environment.

    Args:
        name: Environment variable name.
        default: Value to parse when the variable is unset.

    Returns:
        ``True`` for common enabled values; otherwise ``False``.
    """
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Default application configuration sourced from the environment."""

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/langgraph_chat",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    CHAT_MODEL_PROVIDER = os.getenv("CHAT_MODEL_PROVIDER", "openai")
    CUSTOM_REASONING_GRAPH_ENABLED = env_flag("CUSTOM_REASONING_GRAPH_ENABLED")
    CUSTOM_REASONING_MAX_RESEARCH_ROUNDS = int(
        os.getenv("CUSTOM_REASONING_MAX_RESEARCH_ROUNDS", "2")
    )
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.5")
    DEFAULT_REASONING_EFFORT = os.getenv("DEFAULT_REASONING_EFFORT", "medium")
    MEMORY_EMBEDDING_MODEL = os.getenv(
        "MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    MEMORY_EMBEDDING_DIMENSIONS = int(os.getenv("MEMORY_EMBEDDING_DIMENSIONS", "1536"))
    CONVERSATION_HISTORY_LIMIT = int(os.getenv("CONVERSATION_HISTORY_LIMIT", "24"))
    TOOL_MAX_CONCURRENCY = int(os.getenv("TOOL_MAX_CONCURRENCY", "4"))
    PLATFORM_ADMIN_EMAILS = tuple(
        email.strip().lower()
        for email in os.getenv("PLATFORM_ADMIN_EMAILS", "").split(",")
        if email.strip()
    )
    LANGGRAPH_DATABASE_URL = os.getenv(
        "LANGGRAPH_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/langgraph_chat?sslmode=disable",
    )
    LANGGRAPH_SETUP_SCHEMA = env_flag("LANGGRAPH_SETUP_SCHEMA")
    DEBUG = env_flag("FLASK_DEBUG")


class TestConfig(Config):
    """Isolated configuration for tests that do not call external services."""

    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    OPENAI_API_KEY = ""
    LANGGRAPH_DATABASE_URL = ""
