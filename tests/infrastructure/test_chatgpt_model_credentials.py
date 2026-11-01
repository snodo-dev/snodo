"""Credential resolution refreshes ChatGPT OAuth before model calls."""

import pytest

from snodo.config import ConfigManager, register_chatgpt_oauth_credential_resolver
from snodo.infrastructure import chatgpt_oauth
from snodo.infrastructure import cloud_credentials


def test_oauth_resolution_refreshes_once_and_returns_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    manager.set_value(("providers", "chatgpt", "auth"), "chatgpt-oauth")
    calls = []

    def ensure_token(provider):
        calls.append(provider)
        return "fresh-token"

    monkeypatch.setattr(chatgpt_oauth, "ensure_chatgpt_access_token", ensure_token)
    register_chatgpt_oauth_credential_resolver(cloud_credentials._chatgpt_model_credential)

    assert manager.get_key_for_model("chatgpt/gpt-4o") is None
    assert manager.get_key_for_model("chatgpt/gpt-4o") is None
    assert calls == ["chatgpt", "chatgpt"]


def test_reauthentication_error_propagates_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    manager.set_value(("providers", "chatgpt", "auth"), "chatgpt-oauth")
    message = "ChatGPT sign-in is required; run `snodo models --provider chatgpt --authenticate` again."

    def require_sign_in(provider):
        raise chatgpt_oauth.ChatGPTReauthenticationRequired(message)

    monkeypatch.setattr(chatgpt_oauth, "ensure_chatgpt_access_token", require_sign_in)
    register_chatgpt_oauth_credential_resolver(cloud_credentials._chatgpt_model_credential)
    with pytest.raises(chatgpt_oauth.ChatGPTReauthenticationRequired) as error:
        manager.get_key_for_model("chatgpt/gpt-4o")
    assert str(error.value) == message


def test_non_oauth_and_cloud_credentials_keep_existing_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    manager = ConfigManager()
    manager.set_value(("providers", "custom", "api_key"), "custom-key")
    manager.set_value(("cloud", "api_key"), "cloud-key")
    calls = []
    register_chatgpt_oauth_credential_resolver(lambda provider: calls.append(provider))

    assert manager.get_key_for_model("custom/model") == "custom-key"
    assert manager.get_key_for_model("snodo-cloud/model") == "cloud-key"
    assert calls == []
