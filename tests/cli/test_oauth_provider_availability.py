"""OAuth provider availability across listing and run preflight."""

from snodo.cli.commands.models_providers import _configured_provider_names, _list_providers
from snodo.cli.commands.run_preflight import preflight_provider_credential
from snodo.config import ConfigManager, ProviderConfig


def test_oauth_provider_listing_shows_sign_in_state(monkeypatch, capsys):
    provider = ProviderConfig(auth="chatgpt-oauth")
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: {"chatgpt": provider})
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, name: name == "chatgpt")
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_sign_in_state", lambda: {"expires_at": 1})
    assert _configured_provider_names({"chatgpt": provider}) == ["chatgpt"]
    _list_providers({"chatgpt": provider})
    assert "signed in" in capsys.readouterr().out


def test_oauth_provider_listing_prompts_authentication(monkeypatch, capsys):
    provider = ProviderConfig(auth="chatgpt-oauth")
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: {"chatgpt": provider})
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, name: name == "chatgpt")
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_sign_in_state", lambda: None)
    _list_providers({"chatgpt": provider})
    output = capsys.readouterr().out
    assert "not signed in" in output
    assert "snodo models --provider chatgpt --authenticate" in output


def test_run_preflight_oauth_sign_in_state(monkeypatch):
    provider = ProviderConfig(auth="chatgpt-oauth")
    monkeypatch.setattr(ConfigManager, "_provider_for_model", staticmethod(lambda model: "chatgpt"))
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: {"chatgpt": provider})
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, name: True)
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_sign_in_state", lambda: {"expires_at": 1})
    assert preflight_provider_credential("chatgpt/model") is None
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_sign_in_state", lambda: None)
    error = preflight_provider_credential("chatgpt/model")
    assert "chatgpt" in error
    assert "snodo models --provider chatgpt --authenticate" in error


def test_run_preflight_api_key_behavior_unchanged(monkeypatch):
    provider = ProviderConfig(api_key_env="CHATGPT_API_KEY")
    monkeypatch.setattr(ConfigManager, "_provider_for_model", staticmethod(lambda model: "chatgpt"))
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: {"chatgpt": provider})
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, name: False)
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)
    assert "CHATGPT_API_KEY" in preflight_provider_credential("chatgpt/model")
    monkeypatch.setenv("CHATGPT_API_KEY", "key")
    assert preflight_provider_credential("chatgpt/model") is None
