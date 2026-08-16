from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)

target_metadata = current_app.extensions["migrate"].db.metadata


def get_engine():
    """Return the Flask-SQLAlchemy engine used by Alembic."""
    return current_app.extensions["migrate"].db.engine


def run_migrations_offline():
    """Run migrations with a configured URL and no live connection."""
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations within a live database connection."""
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
