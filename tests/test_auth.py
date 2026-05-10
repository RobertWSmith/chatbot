from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash

from .conftest import register


def test_register_login_logout(client, app):
    response = register(client)
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="user@example.com").one()
        assert user.settings is not None
        assert user.check_password("very-secure-password")

    response = client.get("/logout", follow_redirects=True)
    assert response.status_code == 200

    response = client.post(
        "/login",
        data={"email": "user@example.com", "password": "very-secure-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Ask anything" in response.data


def test_login_accepts_password_manager_characters(client, app):
    password = "  exact Pässw0rd from manager!  "
    register(client, email="manager@example.com", password=password)
    client.get("/logout")

    response = client.post(
        "/login",
        data={"email": "manager@example.com", "password": password},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ask anything" in response.data


def test_login_rehashes_legacy_werkzeug_password(client, app):
    password = "legacy-secure-password"
    with app.app_context():
        user = User(email="legacy@example.com", password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/login",
        data={"email": "legacy@example.com", "password": password},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ask anything" in response.data
    with app.app_context():
        user = User.query.filter_by(email="legacy@example.com").one()
        assert user.password_hash.startswith("$argon2")
        assert user.check_password(password)


def test_login_rejects_invalid_hash_without_error(client, app):
    with app.app_context():
        user = User(email="invalid-hash@example.com", password_hash="not-a-real-hash")
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/login",
        data={"email": "invalid-hash@example.com", "password": "any-password"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Invalid email or password" in response.data


def test_register_shows_password_validation_errors(client):
    response = register(client, email="short@example.com", password="short")

    assert response.status_code == 200
    assert b"Field must be at least 12 characters long" in response.data


def test_duplicate_email_rejected(client, app):
    register(client)
    client.get("/logout")
    response = register(client)
    assert b"already exists" in response.data
    with app.app_context():
        assert User.query.count() == 1
