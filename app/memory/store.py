from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from flask import current_app


@contextmanager
def open_memory_store() -> Iterator[object | None]:
    uri = current_app.config.get("LANGGRAPH_DATABASE_URL")
    if not uri:
        yield None
        return
    try:
        from langgraph.store.postgres import PostgresStore
    except ImportError:
        yield None
        return

    with PostgresStore.from_conn_string(uri) as store:
        if current_app.config.get("LANGGRAPH_SETUP_SCHEMA"):
            store.setup()
        yield store


def user_memory_namespace(user_id: int) -> tuple[str, str]:
    return (str(user_id), "memories")
