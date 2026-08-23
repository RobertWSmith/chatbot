from markupsafe import escape

from app.models import DEFAULT_SYSTEM_PROMPT, User

from .conftest import register


def test_system_prompt_has_a_database_default_and_is_rendered(client, app):
    register(client)

    page = client.get("/settings")

    assert page.status_code == 200
    assert b'name="system_prompt"' in page.data
    assert str(escape(DEFAULT_SYSTEM_PROMPT)).encode() in page.data
    with app.app_context():
        settings = User.query.one().settings
        assert settings.system_prompt == DEFAULT_SYSTEM_PROMPT
        assert "system_prompt" not in settings.data


def test_system_prompt_can_be_updated_and_is_stored_in_its_column(client, app):
    register(client)
    custom_prompt = "  You are a patient Python tutor. Prefer small examples.  "

    response = client.patch("/api/settings", json={"system_prompt": custom_prompt})

    assert response.status_code == 200
    assert response.get_json()["settings"]["system_prompt"] == custom_prompt.strip()
    with app.app_context():
        settings = User.query.one().settings
        assert settings.system_prompt == custom_prompt.strip()
        assert "system_prompt" not in settings.data


def test_system_prompt_rejects_empty_non_text_and_oversized_values(client):
    register(client)

    for value in ("   ", None, "x" * 12_001):
        response = client.patch("/api/settings", json={"system_prompt": value})
        assert response.status_code == 400
        assert "system_prompt" in response.get_json()["errors"]


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
