from __future__ import annotations

from flask import Flask, redirect, url_for

from .config import Config
from .extensions import csrf, db, login_manager, migrate


def create_app(config_object: type[Config] | None = None, **overrides: object) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_object: Configuration class to load instead of :class:`Config`.
        **overrides: Individual Flask configuration values to override.

    Returns:
        The configured Flask application.
    """
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    app.config.update(overrides)

    _validate_database_url(app)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        """Load a user for Flask-Login.

        Args:
            user_id: Serialized primary key stored in the session.

        Returns:
            The matching user, or ``None`` when it no longer exists.
        """
        return db.session.get(User, int(user_id))

    from .auth.routes import bp as auth_bp
    from .chat.routes import bp as chat_bp
    from .memory.routes import bp as memory_bp
    from .settings.routes import bp as settings_bp
    from .tenancy.routes import bp as tenancy_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(memory_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(tenancy_bp)

    @app.get("/")
    def index():
        """Redirect the site root to the chat page."""
        return redirect(url_for("chat.chat_home"))

    return app


def _validate_database_url(app: Flask) -> None:
    """Require PostgreSQL outside the isolated test configuration.

    Args:
        app: Application whose database configuration should be checked.

    Raises:
        RuntimeError: If a non-PostgreSQL database is configured outside tests.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if app.config.get("TESTING"):
        return
    if not uri.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Postgres is required. Set DATABASE_URL to a postgresql+psycopg:// URL.")
