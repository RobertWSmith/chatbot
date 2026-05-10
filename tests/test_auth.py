from app.models import User

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


def test_duplicate_email_rejected(client, app):
    register(client)
    client.get("/logout")
    response = register(client)
    assert b"already exists" in response.data
    with app.app_context():
        assert User.query.count() == 1
