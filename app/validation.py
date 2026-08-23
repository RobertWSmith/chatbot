from __future__ import annotations

from copy import deepcopy

from .models import DEFAULT_SYSTEM_PROMPT, default_settings

MODEL_OPTIONS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5-nano",
)
REASONING_OPTIONS = ("none", "low", "medium", "high", "xhigh", "max")
ALLOWED_MODELS = set(MODEL_OPTIONS)
ALLOWED_REASONING = set(REASONING_OPTIONS)
REASONING_PROVIDER_OPTIONS = (("openai", "OpenAI"), ("langgraph", "LangGraph"))
ALLOWED_REASONING_PROVIDERS = {value for value, _label in REASONING_PROVIDER_OPTIONS}
ALLOWED_THEMES = {"system", "light", "dark"}
ALLOWED_FONT_SIZES = {"small", "medium", "large"}
ALLOWED_SPEEDS = {"slow", "normal", "fast"}
MAX_SYSTEM_PROMPT_LENGTH = 12_000
SCALAR_SETTING_RULES = {
    "model_name": ALLOWED_MODELS,
    "reasoning_effort": ALLOWED_REASONING,
    "reasoning_provider": ALLOWED_REASONING_PROVIDERS,
    "theme": ALLOWED_THEMES,
    "font_size": ALLOWED_FONT_SIZES,
    "streaming_speed": ALLOWED_SPEEDS,
}
BOOLEAN_SETTINGS = {"reasoning_summaries_enabled", "memory_enabled", "compact_mode"}
BOOLEAN_MAP_SETTINGS = {"markdown_options", "privacy"}


def validate_settings_update(current: dict, patch: dict) -> dict:
    """Validate and apply a partial user-settings update.

    Args:
        current: Existing effective settings.
        patch: User-supplied settings to apply.

    Returns:
        A complete validated settings dictionary.

    Raises:
        ValueError: If the patch contains unknown keys or invalid values. The
            first exception argument maps invalid fields to error messages.
    """
    next_settings = deepcopy(default_settings())
    next_settings.update(current or {})
    next_settings.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)
    errors: dict[str, str] = {}

    for key, value in patch.items():
        if key in SCALAR_SETTING_RULES:
            if value not in SCALAR_SETTING_RULES[key]:
                errors[key] = f"Unsupported value: {value}"
            else:
                next_settings[key] = value
        elif key in BOOLEAN_SETTINGS:
            if not isinstance(value, bool):
                errors[key] = "Must be true or false."
            else:
                next_settings[key] = value
        elif key in BOOLEAN_MAP_SETTINGS:
            if not isinstance(value, dict):
                errors[key] = "Must be an object."
            else:
                next_settings[key].update(_validate_bool_map(value, key, errors))
        elif key == "system_prompt":
            if not isinstance(value, str):
                errors[key] = "Must be text."
            elif not value.strip():
                errors[key] = "Must not be empty."
            elif len(value.strip()) > MAX_SYSTEM_PROMPT_LENGTH:
                errors[key] = f"Must be no more than {MAX_SYSTEM_PROMPT_LENGTH} characters."
            else:
                next_settings[key] = value.strip()
        else:
            errors[key] = "Unknown setting."

    if errors:
        raise ValueError(errors)
    return next_settings


def _validate_bool_map(value: dict, prefix: str, errors: dict[str, str]) -> dict:
    """Return valid boolean entries and record invalid ones.

    Args:
        value: Nested mapping supplied by the user.
        prefix: Parent setting name used in error paths.
        errors: Mutable error mapping populated for invalid entries.

    Returns:
        A mapping containing only boolean values.
    """
    cleaned = {}
    for child_key, child_value in value.items():
        if not isinstance(child_value, bool):
            errors[f"{prefix}.{child_key}"] = "Must be true or false."
        else:
            cleaned[child_key] = child_value
    return cleaned
