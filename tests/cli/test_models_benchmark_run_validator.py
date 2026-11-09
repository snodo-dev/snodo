"""Benchmark-run integration for one recorded validator."""

import json
from types import SimpleNamespace


def _baseline(tmp_path, monkeypatch):
    from snodo.cli.commands import models_benchmark
    baseline_dir = tmp_path / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1",
        "change_size": {"base_sha": "base", "head_sha": "head"},
        "validator_results": [{"validator_id": "v", "severity": "pass", "justification": "recorded"}],
    }))
    monkeypatch.setattr(models_benchmark, "resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(models_benchmark, "_task_spec", lambda *a: "spec")
    return models_benchmark


def test_benchmark_run_with_validator_replays_only_that_validator(tmp_path, monkeypatch, capsys):
    module = _baseline(tmp_path, monkeypatch)
    from snodo.cli.commands import models_validator_replay, models_validator_run
    from snodo.validators import runner
    from snodo.cli.commands import models_benchmark_notice
    resolved = SimpleNamespace(spec="task", phase="pre_execute", base_commit="base", head_commit="base", result={"severity": "pass", "justification": "recorded"})
    monkeypatch.setattr(models_validator_replay, "resolve_validator_replay_input", lambda *a: resolved)
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda p: SimpleNamespace(initial_mode="initial"))
    monkeypatch.setattr("snodo.infrastructure.state.read_state", lambda p: SimpleNamespace(current_mode="initial"))
    monkeypatch.setattr(runner, "resolve_validator_completion", lambda: (None, "model", object()))
    observed = {}
    monkeypatch.setattr(models_validator_run, "replay_validator", lambda *a, **kw: observed.setdefault("args", (a, kw)) or {"severity": "pass", "justification": "candidate"})
    # setdefault returns a truthy tuple; return the candidate explicitly.
    monkeypatch.setattr(models_validator_run, "replay_validator", lambda *a, **kw: (observed.update(args=(a, kw)) or {"severity": "pass", "justification": "candidate", "error": False}))
    monkeypatch.setattr(models_benchmark_notice, "print_validator_replay_notice", lambda *a: print("notice", file=__import__("sys").stderr))
    result = module.models_benchmark_run_command(SimpleNamespace(plan="p", task="t", model="provider/model", validator="v", json=False))
    assert result == 0
    assert observed["args"][0][3] == "v"
    captured = capsys.readouterr()
    assert "notice" in captured.err
    assert "Validator verdict" in captured.out


def test_benchmark_run_without_validator_is_unchanged(tmp_path, monkeypatch):
    module = _baseline(tmp_path, monkeypatch)
    submitted = {}
    class Manager:
        def __init__(self, root):
            pass
        def submit(self, args):
            submitted.update(args)
            return "job"
        def wait_for(self, job):
            return {"status": "completed"}
    monkeypatch.setattr("snodo.jobs.JobManager", Manager)
    monkeypatch.setattr("snodo.cli.commands.plan_compare.compare_models_command", lambda args: 0)
    assert module.models_benchmark_run_command(SimpleNamespace(plan="p", task="t", model="provider/model", json=False)) == 0
    assert submitted["benchmark"] is True and "validator" not in submitted


def test_validator_without_model_is_an_error(tmp_path, monkeypatch, capsys):
    module = _baseline(tmp_path, monkeypatch)
    result = module.models_benchmark_run_command(SimpleNamespace(plan="p", task="t", validator="v", model=None, json=False))
    assert result == 1
    assert "requires --model" in capsys.readouterr().err


def test_validator_json_output_contains_baseline_and_candidate(tmp_path, monkeypatch, capsys):
    module = _baseline(tmp_path, monkeypatch)
    from snodo.cli.commands import models_validator_replay, models_validator_run
    from snodo.validators import runner
    from snodo.cli.commands import models_benchmark_notice
    resolved = SimpleNamespace(spec="task", phase="pre_execute", base_commit="base", head_commit="base", result={"severity": "pass", "justification": "recorded"})
    monkeypatch.setattr(models_validator_replay, "resolve_validator_replay_input", lambda *a: resolved)
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda p: SimpleNamespace(initial_mode="initial"))
    monkeypatch.setattr("snodo.infrastructure.state.read_state", lambda p: SimpleNamespace(current_mode="initial"))
    monkeypatch.setattr(runner, "resolve_validator_completion", lambda: (None, "model", object()))
    monkeypatch.setattr(models_validator_run, "replay_validator", lambda *a, **kw: {"severity": "blocker", "justification": "candidate", "error": False})
    monkeypatch.setattr(models_benchmark_notice, "print_validator_replay_notice", lambda *a: None)
    assert module.models_benchmark_run_command(SimpleNamespace(plan="p", task="t", model="provider/model", validator="v", json=True)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["baseline"]["severity"] == "pass"
    assert output["candidate"]["severity"] == "blocker"


def test_validator_replay_prints_the_notice_first(tmp_path, monkeypatch, capsys):
    module = _baseline(tmp_path, monkeypatch)
    from snodo.cli.commands import models_validator_replay, models_validator_run
    from snodo.validators import runner
    from snodo.cli.commands import models_benchmark_notice
    resolved = SimpleNamespace(spec="task", phase="pre_execute", base_commit="base", head_commit="base", result={"severity": "pass"})
    monkeypatch.setattr(models_validator_replay, "resolve_validator_replay_input", lambda *a: resolved)
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda p: SimpleNamespace(initial_mode="initial"))
    monkeypatch.setattr("snodo.infrastructure.state.read_state", lambda p: SimpleNamespace(current_mode="initial"))
    monkeypatch.setattr(runner, "resolve_validator_completion", lambda: (None, "model", object()))
    events = []
    monkeypatch.setattr(models_benchmark_notice, "print_validator_replay_notice", lambda *a: events.append("notice"))
    monkeypatch.setattr(models_validator_run, "replay_validator", lambda *a, **kw: (events.append("run") or {"severity": "pass", "error": False}))
    assert module.models_benchmark_run_command(SimpleNamespace(plan="p", task="t", model="provider/model", validator="v", json=False)) == 0
    assert events == ["notice", "run"]
