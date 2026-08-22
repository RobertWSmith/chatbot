from __future__ import annotations

from copy import deepcopy

from .models import default_settings

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
ALLOWED_MODELS = set(MODEL_OPTIONS)
REASONING_OPTIONS = ("none", "low", "medium", "high", "xhigh", "max")
ALLOWED_REASONING = set(REASONING_OPTIONS)
REASONING_PROVIDER_OPTIONS = (("openai", "OpenAI"), ("langgraph", "LangGraph"))
ALLOWED_REASONING_PROVIDERS = {value for value, _label in REASONING_PROVIDER_OPTIONS}
ALLOWED_THEMES = {"system", "light", "dark"}
ALLOWED_FONT_SIZES = {"small", "medium", "large"}
ALLOWED_SPEEDS = {"slow", "normal", "fast"}


def validate_settings_update(current: dict, patch: dict) -> dict:
    next_settings = deepcopy(default_settings())
    next_settings.update(current or {})
    errors: dict[str, str] = {}

    scalar_rules = {
        "model_name": ALLOWED_MODELS,
        "reasoning_effort": ALLOWED_REASONING,
        "reasoning_provider": ALLOWED_REASONING_PROVIDERS,
        "theme": ALLOWED_THEMES,
        "font_size": ALLOWED_FONT_SIZES,
        "streaming_speed": ALLOWED_SPEEDS,
    }
    boolean_keys = {"reasoning_summaries_enabled", "memory_enabled", "compact_mode"}

    for key, value in patch.items():
        if key in scalar_rules:
            if value not in scalar_rules[key]:
                errors[key] = f"Unsupported value: {value}"
            else:
                next_settings[key] = value
        elif key in boolean_keys:
            if not isinstance(value, bool):
                errors[key] = "Must be true or false."
            else:
                next_settings[key] = value
        elif key == "markdown_options":
            if not isinstance(value, dict):
                errors[key] = "Must be an object."
            else:
                next_settings[key].update(_validate_bool_map(value, "markdown_options", errors))
        elif key == "privacy":
            if not isinstance(value, dict):
                errors[key] = "Must be an object."
            else:
                next_settings[key].update(_validate_bool_map(value, "privacy", errors))
        else:
            errors[key] = "Unknown setting."

    if errors:
        raise ValueError(errors)
    return next_settings


def _validate_bool_map(value: dict, prefix: str, errors: dict[str, str]) -> dict:
    cleaned = {}
    for child_key, child_value in value.items():
        if not isinstance(child_value, bool):
            errors[f"{prefix}.{child_key}"] = "Must be true or false."
        else:
            cleaned[child_key] = child_value
    return cleaned
