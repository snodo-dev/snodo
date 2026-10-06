"""Typed dotted-path access to provider, cloud, and notification settings."""

import pytest

from snodo.config import ConfigManager
from snodo.config_validation import ConfigKeyError, get_config_value, is_secret_config_key, set_config_value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("providers.ollama.base_url", "http://localhost:11434/v1"),
        ("providers.ollama.litellm_provider", "openai"),
        ("cloud.sync_enabled", True),
        ("notifications.silence_threshold_seconds", 1200),
        ("notifications.events", ["job_finished"]),
    ],
)
def test_set_get_round_trip(tmp_path, key, value):
    manager = ConfigManager(config_dir=tmp_path)
    set_config_value(manager, key, value)
    assert get_config_value(manager, key) == value


def test_setting_provider_base_url_creates_provider_entry(tmp_path):
    manager = ConfigManager(config_dir=tmp_path)
    set_config_value(manager, "providers.vllm.base_url", "http://localhost:8000/v1")
    assert manager.load()["providers"]["vllm"]["base_url"] == "http://localhost:8000/v1"


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigKeyError, match="Unknown config key: cloud.typo"):
        set_config_value(ConfigManager(config_dir=tmp_path), "cloud.typo", "x")


def test_wrong_type_is_rejected(tmp_path):
    with pytest.raises(ConfigKeyError, match="Invalid config value"):
        set_config_value(ConfigManager(config_dir=tmp_path), "cloud.sync_enabled", "yes")


def test_secret_fields_are_identifiable():
    assert is_secret_config_key("providers.ollama.api_key_env")
    assert is_secret_config_key("cloud.api_key")
    assert is_secret_config_key("notifications.targets.0.url")
    assert not is_secret_config_key("providers.ollama.base_url")
