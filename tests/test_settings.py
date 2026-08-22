from app.extensions import db
from app.models import User

from .conftest import register

GPT_5_6_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
GPT_5_6_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def test_settings_patch_validates_values(client):
    """Ensure invalid updates fail and valid settings persist."""
    register(client)
    response = client.patch("/api/settings", json={"model_name": "not-a-model"})
    assert response.status_code == 400
    assert "model_name" in response.get_json()["errors"]

    response = client.patch(
        "/api/settings",
        json={
            "privacy": {"save_chat_history": "yes"},
            "unsupported_setting": True,
        },
    )
    assert response.status_code == 400
    errors = response.get_json()["errors"]
    assert "privacy.save_chat_history" in errors
    assert "unsupported_setting" in errors

    response = client.patch(
        "/api/settings",
        json={
            "model_name": "gpt-5.5",
            "reasoning_effort": "high",
            "memory_enabled": False,
            "privacy": {"allow_memory_proposals": False},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["settings"]["reasoning_effort"] == "high"
    assert payload["settings"]["memory_enabled"] is False
    assert payload["settings"]["privacy"]["allow_memory_proposals"] is False
    assert payload["settings"]["privacy"]["save_chat_history"] is True


def test_gpt_5_6_model_and_reasoning_settings_are_supported(client):
    """Ensure every GPT-5.6 model and reasoning effort can be saved."""
    register(client)

    for model in GPT_5_6_MODELS:
        response = client.patch("/api/settings", json={"model_name": model})
        assert response.status_code == 200
        assert response.get_json()["settings"]["model_name"] == model

    for effort in GPT_5_6_REASONING_EFFORTS:
        response = client.patch("/api/settings", json={"reasoning_effort": effort})
        assert response.status_code == 200
        assert response.get_json()["settings"]["reasoning_effort"] == effort

    response = client.patch("/api/settings", json={"reasoning_effort": "minimal"})
    assert response.status_code == 400


def test_legacy_minimal_reasoning_effort_is_normalized(client):
    """Ensure legacy settings use the supported none reasoning effort."""
    register(client)

    with client.application.app_context():
        user = User.query.one()
        user.settings.data = {**user.settings.data, "reasoning_effort": "minimal"}
        db.session.commit()

        assert user.settings.merged()["reasoning_effort"] == "none"
