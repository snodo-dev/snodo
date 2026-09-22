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
    (job_dir / "state.json").write_text(json.dumps({
        "task_id": "t", "status": "completed", "completed_at": 2,
        "cost": {"change_size": {"base_sha": "base", "head_sha": "head", "paths": ["a.py"], "lines_added": 1, "lines_deleted": 0},
                  "provenance": {"model": "candidate/model"}},
    }))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.plan_compare._candidate_patch", lambda root, size: diff)

    assert compare_models_command(SimpleNamespace(plan="p", task="t", job=None, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "snodo.models-compare.v1"
    assert payload["signals"]["content"]["f1"] == 1
