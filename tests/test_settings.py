from .conftest import register


def test_settings_patch_validates_values(client):
    register(client)
    response = client.patch("/api/settings", json={"model_name": "not-a-model"})
    assert response.status_code == 400
    assert "model_name" in response.get_json()["errors"]

    response = client.patch("/api/settings", json={"reasoning_provider": "unknown"})
    assert response.status_code == 400
    assert "reasoning_provider" in response.get_json()["errors"]

    response = client.patch(
        "/api/settings",
        json={
            "model_name": "gpt-5.5",
            "reasoning_effort": "high",
            "reasoning_provider": "langgraph",
            "memory_enabled": False,
            "privacy": {"allow_memory_proposals": False},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["settings"]["reasoning_effort"] == "high"
    assert payload["settings"]["reasoning_provider"] == "langgraph"
    assert payload["settings"]["memory_enabled"] is False
    assert payload["settings"]["privacy"]["allow_memory_proposals"] is False
