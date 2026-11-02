"""Tests for explicit recon model selection."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from snodo.cli.commands import recon_cmd


@pytest.fixture
def wired_recon(monkeypatch):
    submitted = []

    class Manager:
        def __init__(self, root):
            pass

        def submit(self, query, paths, lanes, **kwargs):
            submitted.append(lanes)
            return "recon-id"

        def shutdown(self, **kwargs):
            pass

        def get_status(self, recon_id):
            return {"results": [{"agent": "agent", "model": "model", "result": "done"}]}

    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: ".")
    monkeypatch.setattr("snodo.config.ConfigManager.load", lambda self: {"llm": {"recon": {"models": ["configured/model"]}}})
    monkeypatch.setattr(recon_cmd, "__name__", recon_cmd.__name__)
    monkeypatch.setitem(__import__("sys").modules, "snodo.recon", SimpleNamespace(
        RECON_DEFAULT_MAX_TOKENS=100, RECON_DEFAULT_MAX_TOOL_TURNS=3,
        ReconManager=Manager,
        resolve_recon_agents_with_notice=lambda **kwargs: ([["configured/model"]], None),
        resolve_agent_model=lambda model: model,
    ))
    from snodo.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "provider_for_model", staticmethod(lambda model: "openai" if model != "unknown-model" else None))
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: {"openai": object()})
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: "key")
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, provider: False)
    return submitted


def test_explicit_models_define_separate_lanes(wired_recon, capsys):
    args = SimpleNamespace(query="q", paths=["./"], num_agents=None, models=["openai/a", "openai/b"])
    assert recon_cmd.recon_command(args) == 0
    assert wired_recon == [[["openai/a"], ["openai/b"]]]
    assert "Agents: openai/a, openai/b" in capsys.readouterr().out


@pytest.mark.parametrize("models,expected", [
    (["openai/a,openai/b"], [[["openai/a", "openai/b"]]]),
    (["openai/a", "openai/a"], [[["openai/a"], ["openai/a"]]]),
])
def test_explicit_model_lanes(wired_recon, models, expected):
    args = SimpleNamespace(query="q", paths=["./"], num_agents=None, models=models)
    assert recon_cmd.recon_command(args) == 0
    assert wired_recon == expected


def test_models_and_agent_count_refused_before_dispatch(wired_recon, capsys):
    args = SimpleNamespace(query="q", paths=["./"], num_agents=2, models=["openai/a"])
    assert recon_cmd.recon_command(args) == 1
    assert not wired_recon
    assert "count comes from the number of --model values" in capsys.readouterr().err


def test_unknown_model_refused_before_dispatch(wired_recon, capsys):
    args = SimpleNamespace(query="q", paths=["./"], num_agents=None, models=["unknown-model"])
    assert recon_cmd.recon_command(args) == 1
    assert not wired_recon
    assert "unknown-model" in capsys.readouterr().err


def test_no_model_uses_configuration_unchanged(wired_recon):
    args = SimpleNamespace(query="q", paths=["./"], num_agents=None)
    assert recon_cmd.recon_command(args) == 0
    assert wired_recon == [[["configured/model"]]]


def test_cli_exposes_repeatable_model_option():
    from typer import Typer
    app = Typer()
    recon_cmd.register(app)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--model" in result.stdout
