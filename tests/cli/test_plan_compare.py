import json
from types import SimpleNamespace

from snodo.cli.commands.plan_compare import compare_evidence, compare_models_command


def _record(status="completed", paths=None, model="model/test"):
    return {
        "status": status,
        "model": model,
        "change_size": {
            "paths": paths or ["a.py"],
            "lines_added": 2,
            "lines_deleted": 1,
            "base_sha": "base",
            "head_sha": "head",
        },
    }


def test_content_f1_is_dominant_and_size_is_report_only():
    baseline = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n-old\n+good\n"
    candidate = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n-old\n+other\n"
    result = compare_evidence(_record(), baseline, _record(model="candidate/model"), candidate)

    assert result["signals"]["content"]["precision"] == 0.5
    assert result["signals"]["content"]["recall"] == 0.5
    assert result["signals"]["content"]["f1"] == 0.5
    assert result["signals"]["change_size"]["scored"] is False
    assert result["delta_score"] < 0


def test_exact_patch_and_same_outcome_are_neutral():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n-old\n+good\n"
    result = compare_evidence(_record(), diff, _record(model="candidate/model"), diff)

    assert result["delta_score"] == 0
    assert result["direction"] == "same"
    assert result["models"] == {"baseline": "model/test", "candidate": "candidate/model"}


def test_missing_baseline_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path)
    )
    result = compare_models_command(SimpleNamespace(
        compare=True, plan="p", task="t", job=None, json=False,
    ))
    assert result == 1
    assert "Baseline not found" in capsys.readouterr().err


def test_json_output_contains_all_signals(tmp_path, monkeypatch, capsys):
    baseline_dir = tmp_path / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "plan": "p", "task": "t",
        "model": "baseline/model", "status": "completed",
        "change_size": {"paths": ["a.py"], "lines_added": 1, "lines_deleted": 0},
    }))
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+good\n"
    (baseline_dir / "solution.diff").write_text(diff)
    job_dir = tmp_path / ".snodo" / "jobs" / "j1"
    job_dir.mkdir(parents=True)
    (job_dir / "task.json").write_text(json.dumps({
        "task_id": "t", "task_plan": "benchmark-a1b2c3", "model": "candidate/model",
        "base": "base", "branch": "benchmark/p/t/a1b2c3",
    }))
    (job_dir / "state.json").write_text(json.dumps({
        "status": "completed", "completed_at": 2,
        "cost": {"change_size": None},
    }))
    # The baseline's terminal task record coexists with the candidate job. It
    # must never be mistaken for the explicitly benchmarked candidate.
    task_state = tmp_path / ".snodo" / "tasks" / "t" / "state.json"
    task_state.parent.mkdir(parents=True)
    task_state.write_text(json.dumps({
        "status": "completed", "cost": {"change_size": {
            "base_sha": "baseline-base", "head_sha": "baseline-head", "paths": ["baseline.py"],
        }, "provenance": {"model": "baseline/model"}},
    }))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.plan_compare._candidate_patch", lambda root, size: diff)
    git_results = iter(["head\n", "a.py\n", "1\t0\ta.py\n"])
    monkeypatch.setattr(
        "snodo.cli.commands.plan_compare.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(git_results)),
    )

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job=None, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "snodo.models-compare.v1"
    assert payload["signals"]["content"]["f1"] == 1
    assert payload["models"] == {"baseline": "baseline/model", "candidate": "candidate/model"}
    assert payload["benchmark"]["branch"] == "benchmark/p/t/a1b2c3"
    assert payload["signals"]["change_size"]["candidate_lines"] == 1
    assert "baseline" in payload["run_metrics"]
    assert "candidate" in payload["run_metrics"]
    assert payload["run_metrics"]["candidate"]["post_validation_findings"] is None


def test_compare_resolves_real_job_identity_and_rejects_wrong_explicit_job(tmp_path, monkeypatch, capsys):
    baseline_dir = tmp_path / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "plan": "p", "task": "t",
        "change_size": {"paths": ["a.py"]},
    }))
    (baseline_dir / "solution.diff").write_text("")
    jobs_dir = tmp_path / ".snodo" / "jobs"
    for job_id, task_data in (("right", {"task_id": "t", "task_plan": "benchmark-x", "model": "m",
                                          "base": "base", "branch": "benchmark/p/t/x"}),
                              ("wrong", {"task_id": "t", "task_plan": "other"})):
        job_dir = jobs_dir / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "task.json").write_text(json.dumps(task_data))
        (job_dir / "state.json").write_text(json.dumps({"status": "completed"}))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.plan_compare._candidate_patch", lambda root, size: "")

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job="wrong", json=False)) == 1
    assert "not a completed benchmark candidate" in capsys.readouterr().err


def _write_candidate(root, status, halt=None):
    baseline_dir = root / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "plan": "p", "task": "t",
        "model": "base-model", "change_size": {"paths": ["a.py"], "lines_added": 1, "lines_deleted": 0},
    }))
    (baseline_dir / "solution.diff").write_text("")
    job_dir = root / ".snodo" / "jobs" / "job1"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "task.json").write_text(json.dumps({
        "task_id": "t", "task_plan": "benchmark-ref", "model": "candidate",
        "base": "base", "branch": "benchmark/p/t/ref",
    }))
    state = {"status": status, "halt": halt or {}, "cost": {}}
    (job_dir / "state.json").write_text(json.dumps(state))


def _run_candidate(root, monkeypatch, capsys, status, halt=None, json_out=True):
    _write_candidate(root, status, halt)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(root))
    monkeypatch.setattr("snodo.cli.commands.plan_compare.subprocess.run", lambda *a, **k: SimpleNamespace(stdout="head\n" if "rev-parse" in a[0] else ""))
    result = compare_models_command(SimpleNamespace(plan="p", task="t", job="job1", json=json_out))
    output = capsys.readouterr()
    return result, json.loads(output.out) if json_out else output.out, output.err


def test_completed_candidate_output_is_unchanged(tmp_path, monkeypatch, capsys):
    code, payload, _ = _run_candidate(tmp_path, monkeypatch, capsys, "completed")
    assert code == 0
    assert payload["benchmark"]["status"] == "completed"
    assert payload["status"] == "completed"


def test_failed_candidate_without_patch_is_reported(tmp_path, monkeypatch, capsys):
    code, payload, _ = _run_candidate(tmp_path, monkeypatch, capsys, "failed", {"halt_type": "blocker", "reason": "stopped"})
    assert code == 0
    assert payload["status"] == "failed"
    assert payload["halt_type"] == "blocker"
    assert payload["signals"]["content"]["candidate_lines"] == 0
    assert payload["reason"] == "stopped"


def test_failed_candidate_partial_patch_is_reported(tmp_path, monkeypatch, capsys):
    _write_candidate(tmp_path, "failed")
    state_path = tmp_path / ".snodo" / "jobs" / "job1" / "state.json"
    state = json.loads(state_path.read_text())
    state["cost"]["change_size"] = {"base_sha": "base", "head_sha": "head", "paths": ["a.py"], "lines_added": 1}
    state_path.write_text(json.dumps(state))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.plan_compare._candidate_patch", lambda *a: "+partial\n")
    monkeypatch.setattr("snodo.cli.commands.plan_compare.subprocess.run", lambda *a, **k: SimpleNamespace(stdout="head\n"))
    assert compare_models_command(SimpleNamespace(plan="p", task="t", job="job1", json=True)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_blocked_candidate_human_report_and_redacts_tokens(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret-value")
    code, output, _ = _run_candidate(tmp_path, monkeypatch, capsys, "blocked", {"halt_type": "blocker", "reason": "Bearer private-secret-value\n\x1b[31m denied"}, False)
    assert code == 0
    assert "blocked" in output and "blocker" in output
    assert "private-secret-value" not in output
    reason_line = next(line for line in output.splitlines() if "blocker reason:" in line)
    assert "\x1b" not in reason_line and "[31m" in reason_line


def test_running_job_is_refused(tmp_path, monkeypatch, capsys):
    _write_candidate(tmp_path, "running")
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    assert compare_models_command(SimpleNamespace(plan="p", task="t", job="job1", json=False)) == 1
    assert "not a completed benchmark candidate" in capsys.readouterr().err

def test_token_shaped_reason_redacted_in_json(tmp_path, monkeypatch, capsys):
    code, payload, _ = _run_candidate(tmp_path, monkeypatch, capsys, "failed", {"reason": "failed with sk-abcdefgh12345678"})
    assert code == 0
    assert "sk-abcdefgh" not in payload["reason"]


def test_missing_candidate_job_is_reported(tmp_path, monkeypatch, capsys):
    baseline_dir = tmp_path / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "plan": "p", "task": "t",
    }))
    (baseline_dir / "solution.diff").write_text("")
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job="missing", json=False)) == 1
    assert "not a completed benchmark candidate" in capsys.readouterr().err


def test_human_report_shows_unavailable_metrics_without_usage(tmp_path, monkeypatch, capsys):
    code, output, _ = _run_candidate(tmp_path, monkeypatch, capsys, "completed", json_out=False)

    assert code == 0
    assert "Run metrics (baseline -> candidate; not scored):" in output
    assert "coder time: n/a -> n/a" in output
    assert "coder turns: n/a -> n/a" in output
    assert "candidate coder output tok/s: n/a" in output
    assert "candidate post-validation findings: n/a" in output
    assert "input tokens are not directly comparable" in output


def test_candidate_evidence_without_job_id_is_reported(tmp_path, monkeypatch, capsys):
    baseline_dir = tmp_path / ".snodo" / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "plan": "p", "task": "t",
    }))
    (baseline_dir / "solution.diff").write_text("")
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job=None, json=False)) == 1
    assert "Benchmark candidate job evidence not found" in capsys.readouterr().err


def test_candidate_without_model_is_rejected(tmp_path, monkeypatch, capsys):
    _write_candidate(tmp_path, "completed")
    task_path = tmp_path / ".snodo" / "jobs" / "job1" / "task.json"
    task = json.loads(task_path.read_text())
    task.pop("model")
    task_path.write_text(json.dumps(task))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job="job1", json=False)) == 1
    assert "has no model in task.json" in capsys.readouterr().err
