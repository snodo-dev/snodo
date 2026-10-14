"""Routing and credential behavior for the Snodo Cloud model gateway."""

from snodo.config import ConfigManager, DEFAULT_CLOUD_MODELS_URL


def test_snodo_cloud_model_routes_to_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    assert ConfigManager._provider_for_model("snodo-cloud/model-id") == "snodo-cloud"
    assert ConfigManager.resolve_litellm_provider("snodo-cloud/model-id") == "openai"
    assert ConfigManager.resolve_litellm_model("snodo-cloud/model-id") == "openai/model-id"
    assert ConfigManager.resolve_api_base("snodo-cloud/model-id") == DEFAULT_CLOUD_MODELS_URL
    assert ConfigManager.resolve_litellm_provider("gpt-4o") == "openai"
    assert manager.get_key_for_model("snodo-cloud/model-id") is None


def test_snodo_cloud_uses_configured_models_url_and_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    manager.set_value(("cloud", "models_url"), "https://gateway.example/v1")
    manager.set_value(("cloud", "api_key"), "configured-cloud-key")
    assert ConfigManager.resolve_api_base("snodo-cloud/model-id") == "https://gateway.example/v1"
    assert manager.get_key_for_model("snodo-cloud/model-id") == "configured-cloud-key"


def test_models_url_is_accepted_by_both_strict_config_schemas(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import sys

    import yaml

    from snodo.config_validation import _UserConfig
    from snodo.config_validation import validate_config as validate_cli_config

    # CLI schema validation checks user YAML and supports dotted reads/writes.
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    manager.config_path.write_text(yaml.safe_dump({"cloud": {"models_url": "https://gateway.example/v1"}}))
    assert _UserConfig.model_validate(manager.load()).cloud.models_url == "https://gateway.example/v1"
    assert validate_cli_config(manager) == []

    # Core's independently maintained schema accepts the same key too.
    core_path = Path(__file__).parents[2] / "packages/snodo-core/src/snodo/config_validation.py"
    spec = importlib.util.spec_from_file_location("snodo_core_config_validation", core_path)
    core_validation = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core_validation
    spec.loader.exec_module(core_validation)
    assert core_validation._Root.model_validate({"cloud": {"models_url": "https://gateway.example/v1"}})
