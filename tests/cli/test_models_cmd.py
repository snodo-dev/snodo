"""Tests for snodo models CLI command (models_cmd.py).

FILE: tests/cli/test_models_cmd.py
"""

import json
import subprocess

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from snodo.cli.commands.models_cmd import (
    _apply_discrete_filters,
    _format_context,
    _get_models,
    _lookup_context,
    _lookup_price,
    _read_cache,
    _write_cache,
    models_check_command,
    models_command,
    models_set_baseline_command,
    register,
)
from snodo.cli.commands.models_check import run_canary_call


@pytest.fixture
def mock_providers_config():
    """Mock ConfigManager providers."""
    class DummyProviderConfig:
        def __init__(self, api_key=None, api_key_env=None, base_url=None, models_endpoint=None, extra_headers=None):
            self.api_key = api_key
            self.api_key_env = api_key_env
            self.base_url = base_url
            self.models_endpoint = models_endpoint
            self.extra_headers = extra_headers

    return {
        "openai": DummyProviderConfig(api_key="sk-test-key"),
        "anthropic": DummyProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
        "ollama": DummyProviderConfig(api_key="ollama-key", base_url="http://localhost:11434/v1"),
    }


# ============================================================================
# 1. Provider listing tests (_list_providers / models_command)
# ============================================================================

def test_models_command_list_providers_happy_path(mock_providers_config, monkeypatch, capsys):
    """models_command lists configured providers when keys are set."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    args = SimpleNamespace(provider=None, flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Configured providers:" in out
    assert "openai" in out
    assert "anthropic" in out


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def test_set_baseline_stores_recorded_task_solution_and_replaces_it(tmp_path, monkeypatch):
    """The baseline uses the job's task commits, not the operator's HEAD."""
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("base\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")
    base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _git(root, "checkout", "-qb", "task/demo/task-1-3")
    (root / "solution.txt").write_text("task solution\n")
    _git(root, "add", "solution.txt")
    _git(root, "commit", "-qm", "task solution")
    head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _git(root, "checkout", "-q", "main")
    (root / ".snodo" / "plans" / "demo").mkdir(parents=True)
    job_dir = root / ".snodo" / "jobs" / "j_task"
    job_dir.mkdir(parents=True)
    (job_dir / "task.json").write_text(json.dumps({"task_id": "1.3", "task_plan": "demo"}))
    (job_dir / "state.json").write_text(json.dumps({
        "status": "completed",
        "cost": {
            "change_size": {
                "base_sha": base_sha, "head_sha": head_sha, "paths": ["solution.txt"],
                "files_changed": 1, "lines_added": 1, "lines_deleted": 0,
            },
            "provenance": {"model": "openai/task-model", "coder": "opencode-cli"},
        },
        "halt": {"validator_results": [{"validator_id": "tests", "passed": True}]},
    }))

    plan_dir = root / ".snodo" / "plans" / "demo"
    assert plan_dir.is_dir()
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_project_root", lambda: str(root))

    args = SimpleNamespace(set_baseline=True, plan="demo", task="1.3", json=False)
    assert models_set_baseline_command(args) == 0
    record_path = root / ".snodo" / "baselines" / "demo" / "1.3" / "baseline.json"
    diff_path = record_path.parent / "solution.diff"
    first = json.loads(record_path.read_text())
    assert first["model"] == "openai/task-model"
    assert first["coder"] == "opencode-cli"
    assert first["change_size"]["files_changed"] == 1
    assert first["change_size"]["base_sha"] == base_sha
    assert first["change_size"]["head_sha"] == head_sha
    assert "solution.txt" in diff_path.read_text()
    assert "task solution" in diff_path.read_text()
    assert "README.md" not in diff_path.read_text()

    assert models_set_baseline_command(args) == 0
    second = json.loads(record_path.read_text())
    assert second["diff_sha256"] == first["diff_sha256"]


def test_set_baseline_requires_completed_task(tmp_path, monkeypatch):
    plan_dir = tmp_path / ".snodo" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    task_dir = tmp_path / ".snodo" / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text('{"status": "blocked"}')
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_project_root", lambda: str(tmp_path))
    args = SimpleNamespace(set_baseline=True, plan="demo", task="task-1", json=False)
    assert models_set_baseline_command(args) == 1


def test_models_command_no_providers_configured(monkeypatch, capsys):
    """models_command informs user when no provider keys are configured."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: {})

    args = SimpleNamespace(provider=None, flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No providers configured." in out


def test_models_check_recovers_from_temperature_refusal(monkeypatch, capsys):
    """A model refusing temperature is still healthy when the call recovers."""
    monkeypatch.setattr(
        "snodo.cli.commands.models_cmd._configured_models",
        lambda: [("coder", "openai/temperature-limited")],
    )

    calls = []

    def canary(model):
        def completion(**kwargs):
            calls.append(kwargs.copy())
            if "temperature" in kwargs:
                error = RuntimeError("Unsupported parameter: 'temperature' is not supported")
                error.status_code = 400
                raise error

        run_canary_call(model, completion)

    monkeypatch.setattr("snodo.cli.commands.models_cmd._run_canary_call", canary)

    assert models_check_command(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "OK       openai/temperature-limited (coder)" in out
    assert len(calls) == 2
    assert "temperature" not in calls[1]


def test_models_check_recovers_from_forced_tool_choice_refusal(monkeypatch, capsys):
    """A model refusing forced tool choice is still healthy when unforced."""
    monkeypatch.setattr(
        "snodo.cli.commands.models_cmd._configured_models",
        lambda: [("validator", "openai/unforced-tools")],
    )

    calls = []

    def canary(model):
        def completion(**kwargs):
            calls.append(kwargs.copy())
            if "tool_choice" in kwargs:
                error = RuntimeError("tool_choice is not supported by this provider")
                error.status_code = 400
                raise error

        run_canary_call(model, completion)

    monkeypatch.setattr("snodo.cli.commands.models_cmd._run_canary_call", canary)

    assert models_check_command(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "OK       openai/unforced-tools (validator)" in out
    assert len(calls) == 2
    assert "tool_choice" not in calls[1]


def test_models_check_keeps_credential_rejection_failed(monkeypatch, capsys):
    """A credential rejection is not a recoverable parameter refusal."""
    monkeypatch.setattr(
        "snodo.cli.commands.models_cmd._configured_models",
        lambda: [("coder", "openai/bad-credential")],
    )

    def canary(model):
        def completion(**kwargs):
            error = RuntimeError("invalid_api_key: authentication failed")
            error.status_code = 401
            raise error

        run_canary_call(model, completion)

    monkeypatch.setattr("snodo.cli.commands.models_cmd._run_canary_call", canary)

    assert models_check_command(SimpleNamespace()) == 1
    out = capsys.readouterr().out
    assert "FAILED   openai/bad-credential (coder): invalid_api_key" in out


def test_models_check_does_not_send_subprocess_coder_to_litellm(monkeypatch, capsys):
    """A subprocess coder is not a provider canary and must not be failed by litellm."""
    monkeypatch.setattr(
        "snodo.cli.commands.models_cmd._configured_models",
        lambda: [("coder", "opencode-cli/openai/gpt-4o")],
    )
    monkeypatch.setattr(
        "snodo.coders.availability.check_coder_available",
        lambda coder_name: None,
    )

    def canary(_model):
        raise AssertionError("subprocess coder must not reach litellm")

    monkeypatch.setattr("snodo.cli.commands.models_cmd._run_canary_call", canary)

    assert models_check_command(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "NOT CHECKABLE opencode-cli/openai/gpt-4o (coder)" in out
    assert "LLM Provider" not in out


def test_models_command_unconfigured_provider_failure(mock_providers_config, monkeypatch, capsys):
    """models_command returns 1 when requested provider is not configured."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    args = SimpleNamespace(provider="nonexistent", flush=False)
    res = models_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Provider not configured: nonexistent" in err


# ============================================================================
# 2. Model listing & discovery tests
# ============================================================================

def test_models_command_provider_models_happy_path(mock_providers_config, monkeypatch, capsys):
    """models_command lists discovered models for a provider."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    sample_models = [
        {"id": "gpt-4o", "full_string": "openai/gpt-4o", "context_window": 128000},
        {"id": "gpt-3.5-turbo", "full_string": "openai/gpt-3.5-turbo", "context_window": 16384},
    ]
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: sample_models)

    args = SimpleNamespace(provider="openai", flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Provider: openai" in out
    assert "openai/gpt-4o" in out
    assert "2 model(s) from openai" in out


def test_models_command_no_models_discovered(mock_providers_config, monkeypatch, capsys):
    """models_command prints message when no models are discovered."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: [])

    args = SimpleNamespace(provider="openai", flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No models discovered for openai" in out


def test_generic_openai_compatible_discovery_stubbed(mock_providers_config, monkeypatch, tmp_path, capsys):
    """Generic OpenAI-compatible discovery path against a stubbed httpx endpoint (Ollama Cloud)."""
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", lambda name: {})

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "llama3:8b", "display_name": "Llama 3 8B", "context_window": 8192},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    mock_httpx = MagicMock()
    mock_httpx.get.return_value = mock_response
    monkeypatch.setitem(__import__("sys").modules, "httpx", mock_httpx)

    args = SimpleNamespace(provider="ollama", flush=True)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "ollama/llama3:8b" in out
    assert mock_httpx.get.called
    call_url = mock_httpx.get.call_args_list[0][0][0]
    assert call_url == "http://localhost:11434/v1/models"


def test_get_models_discovery_exception_fallback(mock_providers_config, monkeypatch, capsys):
    """_get_models prints error on discovery failure and falls back to cache."""
    pc = mock_providers_config["openai"]

    def failing_discover(pc):
        raise RuntimeError("Network timeout")

    monkeypatch.setitem(
        __import__("snodo.infrastructure.model_discovery", fromlist=["_DISCOVERY_DISPATCH"])._DISCOVERY_DISPATCH,
        "openai",
        failing_discover,
    )
    monkeypatch.setattr("snodo.cli.commands.models_cmd._read_cache", lambda p: [{"id": "cached-model", "full_string": "openai/cached-model"}])

    models = _get_models("openai", pc, force_refresh=True)

    assert len(models) == 1
    assert models[0]["id"] == "cached-model"
    err = capsys.readouterr().err
    assert "Discovery failed: Network timeout" in err


# ============================================================================
# 3. Cache reading & writing tests
# ============================================================================

def test_cache_write_and_read(tmp_path, monkeypatch):
    """_write_cache and _read_cache persist and read model discovery cache."""
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.cli.commands.models_cmd._CACHE_DIR", tmp_path / "models")

    models_data = [{"id": "m1", "full_string": "prov/m1"}]
    _write_cache("prov", models_data)

    cached = _read_cache("prov")
    assert cached == models_data


def test_read_cache_expired_or_corrupt(tmp_path, monkeypatch):
    """_read_cache returns None when cache file is missing, expired, or corrupt."""
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    cache_dir = tmp_path / "models"
    monkeypatch.setattr("snodo.cli.commands.models_cmd._CACHE_DIR", cache_dir)

    # Missing
    assert _read_cache("missing") is None

    # Corrupt
    cache_dir.mkdir(parents=True, exist_ok=True)
    corrupt_file = cache_dir / "corrupt.json"
    corrupt_file.write_text("{invalid json")
    assert _read_cache("corrupt") is None


# ============================================================================
# 4. Discrete filtering tests
# ============================================================================

def test_apply_discrete_filters(monkeypatch):
    """_apply_discrete_filters filters models using shell-safe parameters."""
    models = [
        {"id": "gpt-4o", "display_name": "GPT-4o", "full_string": "openai/gpt-4o", "context_window": 128000},
        {"id": "gpt-3.5-turbo", "display_name": "GPT-3.5 Turbo", "full_string": "openai/gpt-3.5-turbo", "context_window": 16384},
        {"id": "claude-3-opus", "display_name": "Claude 3 Opus", "full_string": "anthropic/claude-3-opus", "context_window": 200000},
    ]

    # Filter by ID substring
    filtered = _apply_discrete_filters(models, id_contains="gpt")
    assert len(filtered) == 2

    filtered_exact = _apply_discrete_filters(
        [{"id": "gemini-3.1-flash-lite"},
         {"id": "gemini-3.1-flash-lite-preview"},
         {"id": "gemini-3.1-flash-lite-image"}],
        model_id="gemini-3.1-flash-lite",
    )
    assert [m["id"] for m in filtered_exact] == ["gemini-3.1-flash-lite"]

    # Filter by min context
    filtered_ctx = _apply_discrete_filters(models, min_context=100000)
    assert len(filtered_ctx) == 2
    assert {m["id"] for m in filtered_ctx} == {"gpt-4o", "claude-3-opus"}

    # Mock litellm costs
    mock_litellm = MagicMock()
    mock_litellm.model_cost = {
        "openai/gpt-4o": {"input_cost_per_token": 0.000005, "output_cost_per_token": 0.000015},
        "openai/gpt-3.5-turbo": {"input_cost_per_token": 0.0000015, "output_cost_per_token": 0.000002},
    }
    monkeypatch.setitem(__import__("sys").modules, "litellm", mock_litellm)

    # Max output cost <= $10 per 1M
    filtered_cost = _apply_discrete_filters(models, max_output_cost=10.0)
    assert len(filtered_cost) == 1
    assert filtered_cost[0]["id"] == "gpt-3.5-turbo"


def test_models_command_no_filters_matched(mock_providers_config, monkeypatch, capsys):
    """models_command prints message when filters exclude all models."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    sample_models = [{"id": "gpt-4o", "full_string": "openai/gpt-4o", "context_window": 128000}]
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: sample_models)

    args = SimpleNamespace(
        provider="openai",
        flush=False,
        id_contains="nonexistent_model",
        max_output_cost=None,
        min_output_cost=None,
        max_input_cost=None,
        min_context=None,
    )
    res = models_command(args)

    assert res == 0
    assert "No models matched the specified filters." in capsys.readouterr().out


# ============================================================================
# 5. Lookup & Typer registration tests
# ============================================================================

def test_lookup_price_and_context(monkeypatch):
    """_lookup_price and _lookup_context query catalog for metadata."""
    mock_lookup = MagicMock(return_value={
        "input_cost": 0.1, "output_cost": 0.3, "context": 131072,
        "found": True, "cost_unit": "per_1m",
    })
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", mock_lookup)

    inp, outp = _lookup_price("openai/gpt-4o")
    assert inp == "$0.10"
    assert outp == "$0.30"

    ctx_str = _lookup_context("openai/gpt-4o")
    assert ctx_str == "128K"


def test_lookup_price_respects_published_unit(monkeypatch):
    """A per-token price (litellm fallback) is scaled to per-1M; a per-1M
    price (models.dev) is printed at its true magnitude."""
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", MagicMock(return_value={
        "input_cost": 0.000005, "output_cost": 0.000015, "context": 0,
        "found": True, "cost_unit": "per_token",
    }))
    inp, outp = _lookup_price("openai/gpt-4o")
    assert inp == "$5.00"
    assert outp == "$15.00"

    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", MagicMock(return_value={
        "input_cost": 0.1, "output_cost": 0.3, "context": 0,
        "found": True, "cost_unit": "per_1m",
    }))
    inp, outp = _lookup_price("openai/@cf/google/gemma-4-26b-a4b-it")
    assert inp == "$0.10"
    assert outp == "$0.30"


def test_lookup_price_distinguishes_absent_kinds(monkeypatch):
    """A model found with no published price prints 'none published'; a model
    not found at all prints 'unknown'."""
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", MagicMock(return_value={
        "input_cost": "unknown", "output_cost": "unknown", "context": 0,
        "found": True, "cost_unit": "per_1m",
    }))
    inp, outp = _lookup_price("ollama/deepseek-v4-flash:0731")
    assert inp == "none published"
    assert outp == "none published"

    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", MagicMock(return_value={
        "input_cost": "unknown", "output_cost": "unknown", "context": 0,
        "found": False, "cost_unit": "per_1m",
    }))
    inp, outp = _lookup_price("ollama/not-in-catalog")
    assert inp == "unknown"
    assert outp == "unknown"


def test_format_context_renders_k_and_m():
    """Context windows render in K/M while the exact value stays available."""
    assert _format_context(1048576) == "1M"
    assert _format_context(262144) == "256K"
    assert _format_context(8192) == "8K"
    assert _format_context(999) == "999"


def test_cli_register_models(mock_providers_config, monkeypatch):
    """Typer app registration exposes models command."""
    app = typer.Typer()
    register(app)

    runner = CliRunner()
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    res = runner.invoke(app, [])
    assert res.exit_code == 0
