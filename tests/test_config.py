from app.config import env_flag


def test_env_flag_accepts_common_true_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("FEATURE_ENABLED", value)
        assert env_flag("FEATURE_ENABLED") is True


def test_env_flag_rejects_false_values(monkeypatch):
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("FEATURE_ENABLED", value)
        assert env_flag("FEATURE_ENABLED") is False
