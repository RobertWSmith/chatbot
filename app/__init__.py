from __future__ import annotations

from flask import Flask, redirect, url_for

from .config import Config
from .extensions import csrf, db, login_manager, migrate


def create_app(config_object: type[Config] | None = None, **overrides: object) -> Flask:
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
        return db.session.get(User, int(user_id))

    from .auth.routes import bp as auth_bp
    from .chat.routes import bp as chat_bp
    from .memory.routes import bp as memory_bp
    from .settings.routes import bp as settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(memory_bp)
    app.register_blueprint(settings_bp)

    @app.get("/")
    def index():
        return redirect(url_for("chat.chat_home"))

    return app


def _validate_database_url(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if app.config.get("TESTING"):
        return
    if not uri.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError(
            "Postgres is required. Set DATABASE_URL to a postgresql+psycopg:// URL."
        )
