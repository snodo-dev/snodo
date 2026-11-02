"""Tests for explicit recon model selection."""

import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from snodo.cli.commands import recon_cmd


def _plain(text: str) -> str:
    """Strip ANSI styling: CI forces colored help, which splits option names."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


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


def args(models=None, model_list=None, num_agents=None):
    return SimpleNamespace(query="q", paths=["./"], num_agents=num_agents, models=models, model_list=model_list)


def test_repeated_model_values_define_agents(wired_recon, capsys):
    assert recon_cmd.recon_command(args(models=["openai/a", "openai/b"])) == 0
    assert wired_recon == [[["openai/a"], ["openai/b"]]]
    assert "Agents: openai/a, openai/b" in capsys.readouterr().out


def test_models_list_expands_in_order(wired_recon):
    assert recon_cmd.recon_command(args(model_list=["openai/a,openai/b"])) == 0
    assert wired_recon == [[["openai/a"], ["openai/b"]]]


def test_both_forms_and_duplicate_models_preserve_order(wired_recon):
    assert recon_cmd.recon_command(args(models=["openai/a"], model_list=["openai/b,openai/a"])) == 0
    assert wired_recon == [[["openai/a"], ["openai/b"], ["openai/a"]]]


@pytest.mark.parametrize("models,model_list", [(["openai/a,openai/b"], None), (None, ["openai/a,,openai/b"])])
def test_comma_model_option_and_empty_entry_behavior(wired_recon, models, model_list, capsys):
    # --model rejects comma syntax; --models ignores empty entries.
    result = recon_cmd.recon_command(args(models=models, model_list=model_list))
    if models:
        assert result == 1
        assert "--models" in capsys.readouterr().err
        assert not wired_recon
    else:
        assert result == 0
        assert wired_recon == [[["openai/a"], ["openai/b"]]]


@pytest.mark.parametrize("models,model_list", [(["openai/a"], None), (None, ["openai/a"])])
def test_agent_count_refused_with_explicit_model_forms(wired_recon, models, model_list, capsys):
    assert recon_cmd.recon_command(args(models=models, model_list=model_list, num_agents=2)) == 1
    assert not wired_recon
    assert "--model or --models" in capsys.readouterr().err


def test_unknown_model_refused_before_dispatch(wired_recon, capsys):
    assert recon_cmd.recon_command(args(model_list=["unknown-model"])) == 1
    assert not wired_recon
    assert "unknown-model" in capsys.readouterr().err


def test_no_model_uses_configuration_unchanged(wired_recon):
    assert recon_cmd.recon_command(args()) == 0
    assert wired_recon == [[["configured/model"]]]


def test_cli_exposes_model_options():
    from typer import Typer
    app = Typer()
    recon_cmd.register(app)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--model" in _plain(result.stdout)
    assert "--models" in _plain(result.stdout)


def test_cli_exposes_repeatable_model_option():
    from typer import Typer
    app = Typer()
    recon_cmd.register(app)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--model" in _plain(result.stdout)


def test_explicit_model_lanes(wired_recon):
    assert recon_cmd.recon_command(args(models=["openai/a"])) == 0
    assert wired_recon == [[["openai/a"]]]


def test_explicit_models_define_separate_lanes(wired_recon):
    assert recon_cmd.recon_command(args(model_list=["openai/a,openai/b"])) == 0
    assert wired_recon == [[["openai/a"], ["openai/b"]]]


def test_models_and_agent_count_refused_before_dispatch(wired_recon, capsys):
    assert recon_cmd.recon_command(args(model_list=["openai/a"], num_agents=2)) == 1
    assert not wired_recon
    assert "--model or --models" in capsys.readouterr().err


def test_mixed_cli_option_order_is_preserved(monkeypatch):
    monkeypatch.setattr("sys.argv", ["snodo", "recon", "q", "--models", "openai/a,openai/b", "--model", "openai/c"])
    assert recon_cmd._models_in_argv_order(["openai/c"], ["openai/a,openai/b"]) == [
        ("--models", "openai/a,openai/b"), ("--model", "openai/c")
    ]
