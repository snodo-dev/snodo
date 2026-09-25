"""Credential selection for complete remote task loops."""

from types import SimpleNamespace

from snodo.remote_host import resolve_task_provider_keys, task_provider_models


def test_remote_credentials_cover_coder_validators_and_classifier(monkeypatch):
    from snodo.config import ConfigManager

    protocol = SimpleNamespace(
        initial_mode="build",
        modes=[SimpleNamespace(mode_id="build", validators=["security", "format"])],
        validators=[
            SimpleNamespace(validator_id="security", scope="task"),
            SimpleNamespace(validator_id="format", scope="task"),
            SimpleNamespace(validator_id="wave_only", scope="wave"),
        ],
    )
    monkeypatch.setattr(
        ConfigManager, "load",
        lambda self: {
            "model": "openai/gpt-coder",
            "llm": {
                "validator": {"model": "anthropic/claude-validator"},
                "classifier": {"model": "google/gemini-classifier"},
            },
        },
    )
    monkeypatch.setattr(ConfigManager, "get_model", lambda self: "openai/gpt-coder")
    monkeypatch.setattr(
        ConfigManager, "get_key_for_model",
        lambda self, model: f"key-for-{ConfigManager._provider_for_model(model)}",
    )

    providers = task_provider_models(protocol, "openai/gpt-coder", "build")
    keys, checks = resolve_task_provider_keys(protocol, "openai/gpt-coder", "build")

    assert set(providers) == {"openai", "anthropic", "google"}
    assert keys == {
        "openai": "key-for-openai",
        "anthropic": "key-for-anthropic",
        "google": "key-for-google",
    }
    assert all(check.ok for check in checks)


def test_remote_credentials_refuse_when_any_provider_key_is_unavailable(monkeypatch):
    from snodo.config import ConfigManager

    protocol = SimpleNamespace(initial_mode="build", modes=[], validators=[])
    monkeypatch.setattr(ConfigManager, "load", lambda self: {"model": "anthropic/claude"})
    monkeypatch.setattr(ConfigManager, "get_model", lambda self: "anthropic/claude")
    monkeypatch.setattr(
        ConfigManager, "get_key_for_model",
        lambda self, model: None if model.startswith("anthropic/") else "coder-secret",
    )

    keys, checks = resolve_task_provider_keys(protocol, "openai/gpt-coder")

    assert keys == {"openai": "coder-secret"}
    assert [(check.name, check.ok) for check in checks] == [
        ("provider_key:openai", True), ("provider_key:anthropic", False),
    ]
    assert "anthropic" in checks[1].detail
