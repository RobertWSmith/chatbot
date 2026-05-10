from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/langgraph_chat",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.5")
    DEFAULT_REASONING_EFFORT = os.getenv("DEFAULT_REASONING_EFFORT", "medium")
    MEMORY_EMBEDDING_MODEL = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
    MEMORY_EMBEDDING_DIMENSIONS = int(os.getenv("MEMORY_EMBEDDING_DIMENSIONS", "1536"))
    CONVERSATION_HISTORY_LIMIT = int(os.getenv("CONVERSATION_HISTORY_LIMIT", "24"))
    LANGGRAPH_DATABASE_URL = os.getenv(
        "LANGGRAPH_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/langgraph_chat?sslmode=disable",
    )
    LANGGRAPH_SETUP_SCHEMA = os.getenv("LANGGRAPH_SETUP_SCHEMA", "0") == "1"
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    OPENAI_API_KEY = ""
    LANGGRAPH_DATABASE_URL = ""
