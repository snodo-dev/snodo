"""Archived plans remain available to model baseline and benchmark commands."""

import json
from types import SimpleNamespace

from snodo.cli.commands.models_baseline import models_set_baseline_command
from snodo.cli.commands.models_benchmark import models_benchmark_run_command


def test_set_baseline_finds_plan_in_archive(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".snodo" / "plans-archive" / "archived").mkdir(parents=True)
    monkeypatch.setattr("snodo.cli.commands.models_baseline.resolve_project_root", lambda: str(root))
    monkeypatch.setattr(
        "snodo.cli.commands.models_baseline._task_job",
        lambda *args: {"state": {"status": "completed", "cost": {
            "change_size": {"base_sha": "base", "head_sha": "head"},
            "provenance": {"model": "test/model"},
        }}},
    )
    monkeypatch.setattr("snodo.cli.commands.models_baseline._git_diff", lambda *args: "diff --git a/a b/a\n")
    assert models_set_baseline_command(SimpleNamespace(plan="archived", task="1.1", json=False)) == 0
    baseline = root / ".snodo" / "baselines" / "archived" / "1.1" / "baseline.json"
    assert json.loads(baseline.read_text())["plan"] == "archived"


def test_benchmark_run_reads_spec_from_archived_plan(tmp_path, monkeypatch):
    root = tmp_path
    plan_dir = root / ".snodo" / "plans-archive" / "archived"
    wave = plan_dir / "wave_1"
    wave.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text("name: archived\nwaves:\n  - id: 1\n    tasks: ['1.1_task']\n")
    (wave / "1.1_task_task.md").write_text("Archived task spec\n")
    baseline_dir = root / ".snodo" / "baselines" / "archived" / "1.1_task"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "schema": "snodo.task-baseline.v1", "change_size": {"base_sha": "base"},
    }))
    monkeypatch.setattr("snodo.cli.commands.models_benchmark.resolve_project_root", lambda: str(root))
    submitted = {}

    class FakeManager:
        def __init__(self, project_root):
            assert project_root == str(root)

        def submit(self, task_args):
            submitted.update(task_args)
            return "job"

        def wait_for(self, job_id):
            return {"status": "completed"}

    monkeypatch.setattr("snodo.jobs.JobManager", FakeManager)
    monkeypatch.setattr("snodo.cli.commands.plan_compare.compare_models_command", lambda args: 0)
    assert models_benchmark_run_command(SimpleNamespace(
        plan="archived", task="1.1_task", model="candidate/model", json=False,
    )) == 0
    assert submitted["description"] == "Archived task spec\n"


def test_set_baseline_still_reports_unknown_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("snodo.cli.commands.models_baseline.resolve_project_root", lambda: str(tmp_path))
    assert models_set_baseline_command(SimpleNamespace(plan="missing", task="1.1", json=False)) == 1
    assert "Plan 'missing' not found." in capsys.readouterr().err
