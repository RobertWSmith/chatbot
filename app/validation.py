from __future__ import annotations

from copy import deepcopy

from .models import default_settings

ALLOWED_MODELS = {
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5-nano",
}
ALLOWED_REASONING = {"minimal", "low", "medium", "high", "xhigh"}
ALLOWED_THEMES = {"system", "light", "dark"}
ALLOWED_FONT_SIZES = {"small", "medium", "large"}
ALLOWED_SPEEDS = {"slow", "normal", "fast"}
SCALAR_SETTING_RULES = {
    "model_name": ALLOWED_MODELS,
    "reasoning_effort": ALLOWED_REASONING,
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
