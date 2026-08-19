from .conftest import register


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
