import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db


@pytest.fixture()
def app():
    """Provide an isolated Flask application with a fresh schema."""
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """Provide a test client for the isolated application."""
    return app.test_client()


def register(client, email="user@example.com", password="very-secure-password"):
    """Register and sign in a test user through the public form."""
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=True,
    )
