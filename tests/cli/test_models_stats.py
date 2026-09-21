"""Tests for snodo models --stats command.

FILE: tests/cli/test_models_stats.py
"""

import json
from pathlib import Path
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from snodo.cli.commands.models_cmd import (
    models_command,
    register,
)


def _create_job(
    jobs_dir: Path,
    job_id: str,
    coder: str,
    status: str = "completed",
    started_at: float = 1000.0,
    completed_at: float = 1060.0,
    usage: list = None,
) -> Path:
    """Helper to create a job directory with state.json and task.json."""
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    task_data = {"coder": coder, "description": f"Task for {job_id}"}
    (job_dir / "task.json").write_text(json.dumps(task_data, indent=2))

    state_data = {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "usage": usage if usage is not None else [],
    }
    (job_dir / "state.json").write_text(json.dumps(state_data, indent=2))
    return job_dir


def test_stats_project_with_no_jobs(tmp_path, monkeypatch, capsys):
    """A project with no jobs renders sensibly and exits 0."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No jobs recorded for this project." in out


def test_stats_no_snodo_dir(tmp_path, monkeypatch, capsys):
    """Directory without .snodo renders sensibly and exits 0."""
    monkeypatch.chdir(tmp_path)

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No jobs recorded for this project." in out


def test_stats_job_with_empty_usage_list(tmp_path, monkeypatch, capsys):
    """A job whose usage list is empty still reports the coder and handles empty model usage."""
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"
    _create_job(jobs_dir, "j_empty_01", coder="litellm", status="completed", started_at=100.0, completed_at=115.0, usage=[])

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Model usage:" in out
    assert "No model calls recorded." in out
    assert "Coder outcomes:" in out
    assert "litellm" in out
    assert "1/1 (100%) (small sample)" in out
    assert "15.0s" in out


def test_stats_known_by_construction_figures(tmp_path, monkeypatch, capsys):
    """Verify statistics against figures known by construction:
    calls across several models and roles, rates, mean duration, and median job duration.
    """
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"

    # Job 1: litellm, completed, duration = 696s (11.6 min)
    # 2 calls:
    #   - gpt-4o: 256 tokens in 2000ms (128 tok/s), cost $0.005, role: validator
    #   - claude-3-5-sonnet: 110 tokens in 2000ms (55 tok/s), cost $0.003, role: coder
    usage_1 = [
        {
            "model": "openai/gpt-4o",
            "prompt_tokens": 1000,
            "completion_tokens": 256,
            "duration_ms": 2000.0,
            "cost": 0.0050,
            "role": "validator",
        },
        {
            "model": "anthropic/claude-3-5-sonnet",
            "prompt_tokens": 500,
            "completion_tokens": 110,
            "duration_ms": 2000.0,
            "cost": 0.0030,
            "role": "coder",
        },
    ]
    _create_job(jobs_dir, "j_01", coder="litellm", status="completed", started_at=1000.0, completed_at=1696.0, usage=usage_1)

    # Job 2: litellm, completed, duration = 552s (9.2 min)
    # 1 call:
    #   - gpt-4o: 256 tokens in 2000ms (128 tok/s), cost $0.005, role: validator
    usage_2 = [
        {
            "model": "openai/gpt-4o",
            "prompt_tokens": 1000,
            "completion_tokens": 256,
            "duration_ms": 2000.0,
            "cost": 0.0050,
            "role": "validator",
        },
    ]
    _create_job(jobs_dir, "j_02", coder="litellm", status="completed", started_at=2000.0, completed_at=2552.0, usage=usage_2)

    # Job 3: litellm, failed, duration = 552s (9.2 min)
    # 1 call:
    #   - gpt-4o: 128 tokens in 1000ms (128 tok/s), cost $0.0025, role: judge
    usage_3 = [
        {
            "model": "openai/gpt-4o",
            "prompt_tokens": 500,
            "completion_tokens": 128,
            "duration_ms": 1000.0,
            "cost": 0.0025,
            "role": "judge",
        },
    ]
    _create_job(jobs_dir, "j_03", coder="litellm", status="failed", started_at=3000.0, completed_at=3552.0, usage=usage_3)

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out

    # Verify Model table:
    # gpt-4o: 3 calls, 640 output tokens, 128.0 tok/s, 1.67s mean duration, $0.0125 cost, roles: judge, validator
    assert "openai/gpt-4o" in out
    assert "640" in out
    assert "128.0 tok/s" in out
    assert "1.67s" in out
    assert "$0.0125" in out
    assert "judge, validator" in out

    # claude-3-5-sonnet: 1 call, 110 output tokens, 55.0 tok/s, 2.00s mean duration, $0.0030 cost, roles: coder
    assert "anthropic/claude-3-5-sonnet" in out
    assert "110" in out
    assert "55.0 tok/s" in out
    assert "2.00s" in out
    assert "$0.0030" in out
    assert "coder" in out

    # Verify Coder table:
    # litellm: 3 jobs, 2 completed (67%), typical duration median(696, 552, 552) = 552s = 9.2 min
    assert "litellm" in out
    assert "2/3 (67%) (small sample)" in out
    assert "9.2 min" in out


def test_stats_unpriced_model_reported_as_unpriced(tmp_path, monkeypatch, capsys):
    """A model with no recorded cost (cost=0.0 or None and no catalog price)
    is reported as 'unpriced' rather than as costing nothing ($0.00).
    """
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"

    monkeypatch.setattr(
        "snodo.infrastructure.model_catalog.lookup",
        lambda name: {"found": False, "input_cost": "unknown", "output_cost": "unknown"},
    )

    usage = [
        {
            "model": "ollama/deepseek-r1:local",
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "duration_ms": 1000.0,
            "cost": 0.0,  # provider recorded 0.0
            "role": "validator",
        },
        {
            "model": "ollama/deepseek-r1:local",
            "prompt_tokens": 300,
            "completion_tokens": 50,
            "duration_ms": 500.0,
            "cost": None,  # no cost recorded
            "role": "validator",
        },
    ]
    _create_job(jobs_dir, "j_local_01", coder="litellm", status="completed", usage=usage)

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out

    assert "ollama/deepseek-r1:local" in out
    assert "unpriced" in out
    # Must NOT report as $0.00
    assert "$0.00" not in out


def test_stats_unpriced_call_estimated_from_catalog(tmp_path, monkeypatch, capsys):
    """When recorded cost is 0.0 or None, but the provider catalog publishes pricing,
    derive an estimated cost labelled as '(est)'.
    """
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"

    # Mock catalog lookup: $0.10 / $0.30 per 1M tokens
    monkeypatch.setattr(
        "snodo.infrastructure.model_catalog.lookup",
        lambda name: {
            "found": True,
            "input_cost": 0.10,
            "output_cost": 0.30,
            "cost_unit": "per_1m",
        },
    )

    # 100,000 prompt tokens * $0.10/1M = $0.01
    # 100,000 completion tokens * $0.30/1M = $0.03
    # Total est cost = $0.0400
    usage = [
        {
            "model": "deepseek/deepseek-chat",
            "prompt_tokens": 100_000,
            "completion_tokens": 100_000,
            "duration_ms": 2000.0,
            "cost": 0.0,
            "role": "validator",
        },
    ]
    _create_job(jobs_dir, "j_est_01", coder="litellm", status="completed", usage=usage)

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "deepseek/deepseek-chat" in out
    assert "$0.0400 (est)" in out


def test_stats_coder_mixed_completed_and_failed_small_sample(tmp_path, monkeypatch, capsys):
    """A coder with a mix of completed and failed jobs reports sample size and labels small sample."""
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"

    # 5 jobs: 3 completed, 2 failed
    _create_job(jobs_dir, "j_01", coder="agy", status="completed", started_at=100.0, completed_at=200.0)
    _create_job(jobs_dir, "j_02", coder="agy", status="completed", started_at=100.0, completed_at=200.0)
    _create_job(jobs_dir, "j_03", coder="agy", status="completed", started_at=100.0, completed_at=200.0)
    _create_job(jobs_dir, "j_04", coder="agy", status="failed", started_at=100.0, completed_at=200.0)
    _create_job(jobs_dir, "j_05", coder="agy", status="failed", started_at=100.0, completed_at=200.0)

    args = SimpleNamespace(stats=True, provider=None)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "agy" in out
    assert "3/5 (60%) (small sample)" in out


def test_stats_provider_filter(tmp_path, monkeypatch, capsys):
    """--provider filters model statistics when combined with --stats."""
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"

    usage = [
        {"model": "openai/gpt-4o", "prompt_tokens": 10, "completion_tokens": 10, "duration_ms": 100.0, "cost": 0.01, "role": "coder"},
        {"model": "anthropic/claude-3-5-sonnet", "prompt_tokens": 10, "completion_tokens": 10, "duration_ms": 100.0, "cost": 0.02, "role": "validator"},
    ]
    _create_job(jobs_dir, "j_01", coder="litellm", usage=usage)

    args = SimpleNamespace(stats=True, provider="openai")
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "openai/gpt-4o" in out
    assert "anthropic/claude-3-5-sonnet" not in out


def test_stats_via_cli_runner(tmp_path, monkeypatch):
    """Typer CLI runner invokes --stats correctly."""
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"
    _create_job(jobs_dir, "j_cli_01", coder="litellm", status="completed", started_at=0.0, completed_at=10.0)

    app = typer.Typer()
    register(app)
    runner = CliRunner()

    res = runner.invoke(app, ["--stats"])
    assert res.exit_code == 0
    assert "Coder outcomes:" in res.stdout
    assert "litellm" in res.stdout


def test_provenance_distinguishes_match_mismatch_and_unreported(tmp_path, monkeypatch, capsys):
    """Provenance reports the three direct outcomes without collapsing absence."""
    monkeypatch.chdir(tmp_path)
    jobs_dir = tmp_path / ".snodo" / "jobs"
    usage = [
        {"timestamp": 3, "model": "subscription/model", "served_model": "subscription/model", "role": "coder"},
        {"timestamp": 2, "model": "subscription/model", "served_model": "deployment/model", "role": "validator"},
        {"timestamp": 1, "model": "subscription/model", "served_model": None, "role": "judge"},
    ]
    _create_job(jobs_dir, "j_provenance", coder="litellm", usage=usage)

    args = SimpleNamespace(provenance=True, provenance_limit=20, json=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "subscription/model" in out
    assert "deployment/model" in out
    assert "match" in out
    assert "mismatch" in out
    assert "unreported" in out
