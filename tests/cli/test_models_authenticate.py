"""Tests for OAuth sign-in via ``snodo models``."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from snodo.cli.commands import models_authenticate as command
from snodo.cli.commands.models_cmd import register


@pytest.fixture
def setup_auth(monkeypatch):
    provider = SimpleNamespace(auth="chatgpt-oauth")
    manager = Mock()
    manager.get_providers.return_value = {"chatgpt": provider}
    manager.provider_uses_oauth.return_value = True
    monkeypatch.setattr("snodo.config.ConfigManager", lambda: manager)
    state = {"value": None}
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_sign_in_state", lambda: state["value"])
    class Lock:
        def __enter__(self):
            return None
        def __exit__(self, *_args):
            return False
    monkeypatch.setattr("snodo.infrastructure.chatgpt_oauth.chatgpt_token_lock", Lock)
    return state


def test_auth_success(setup_auth, monkeypatch, capsys):
    setup_auth["value"] = {"signed_in": True, "expires_at": 4_000_000_000}
    class Authenticator:
        def get_access_token(self):
            print("https://auth.example and code ABCD")
    monkeypatch.setitem(__import__("sys").modules, "litellm.llms.chatgpt.authenticator", SimpleNamespace(Authenticator=Authenticator))
    result = command.models_authenticate_command(SimpleNamespace(provider="chatgpt", json=False, reauthenticate=True))
    assert result == 0
    assert "code ABCD" in capsys.readouterr().out


def test_already_signed_in(setup_auth, capsys):
    setup_auth["value"] = {"signed_in": True, "expires_at": 4_000_000_000}
    assert command.models_authenticate_command(SimpleNamespace(provider="chatgpt")) == 0
    assert "already signed in" in capsys.readouterr().out


def test_api_key_refused(monkeypatch, capsys):
    manager = Mock()
    manager.get_providers.return_value = {"openai": object()}
    manager.provider_uses_oauth.return_value = False
    monkeypatch.setattr("snodo.config.ConfigManager", lambda: manager)
    assert command.models_authenticate_command(SimpleNamespace(provider="openai")) == 1
    assert "API key" in capsys.readouterr().err


@pytest.mark.parametrize("args,message", [
    (SimpleNamespace(provider=None), "requires --provider"),
    (SimpleNamespace(provider="missing"), "not configured"),
])
def test_invalid_provider(args, message, setup_auth, capsys):
    assert command.models_authenticate_command(args) == 1
    assert message in capsys.readouterr().err


def test_json_error(setup_auth, capsys):
    assert command.models_authenticate_command(SimpleNamespace(provider=None, json=True)) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_help_mentions_oauth():
    app = __import__("typer").Typer()
    register(app)
    result = CliRunner().invoke(app, ["models", "--help"], terminal_width=200)
    assert "--authenticate" in result.output
    assert "OAuth" in result.output and "providers only" in result.output


def test_denied_sign_in_is_reported(setup_auth, monkeypatch, capsys):
    class Authenticator:
        def get_access_token(self):
            raise RuntimeError("device authorization denied")
    monkeypatch.setitem(__import__("sys").modules, "litellm.llms.chatgpt.authenticator", SimpleNamespace(Authenticator=Authenticator))
    result = command.models_authenticate_command(SimpleNamespace(provider="chatgpt", reauthenticate=True))
    assert result == 1
    assert "denied" in capsys.readouterr().err
