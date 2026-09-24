"""End-to-end queue journeys across real CLI processes (ADR 053)."""

import json
from importlib.resources import files

import pytest


pytestmark = pytest.mark.e2e


def _plan(snodo_cli, name: str, task_id: str, spec: str) -> None:
    """Write a small on-disk plan fixture that the real CLI can validate/run."""
    plan_dir = snodo_cli.home / ".snodo" / "plans" / name
    task_dir = plan_dir / "wave_1"
    task_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        f"intent: {name}\nname: {name}\nwaves:\n  - id: 1\n    tasks:\n      - {task_id}\n"
    )
    (task_dir / f"{task_id}_task.md").write_text(spec)
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {}}))


def _queues(snodo_cli) -> dict[str, list[str]]:
    return json.loads((snodo_cli.home / ".snodo" / "queues.json").read_text())["queues"]


def _empty_plan(snodo_cli, name: str) -> None:
    plan_dir = snodo_cli.home / ".snodo" / "plans" / name
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        f"intent: {name}\nname: {name}\nwaves:\n  - id: 1\n    tasks: []\n"
    )
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {}}))


def test_blocked_queue_can_be_corrected_while_another_queue_finishes(snodo_cli):
    """A blocked queue stops locally, then a validated front fix resumes it."""
    initialized = snodo_cli(["init", "--template", "2+n", "--yes"])
    assert initialized.returncode == 0, initialized.stderr
    assert snodo_cli(["queue", "create", "parallel"]).returncode == 0

    # 2+n's strict scope constraints reliably stop the mock coder's generated
    # fixture output. The independent queue contains a runnable empty-wave
    # fixture plan, exercising the real runner without sharing file changes.
    _plan(snodo_cli, "blocked", "1.1_blocked", "Implement the requested feature.\n")
    _empty_plan(snodo_cli, "independent-work")
    _plan(snodo_cli, "later", "1.1_later", "Work that follows the blocked plan.\n")

    for name in ("blocked", "later"):
        validation = snodo_cli(["plan", "validate", name])
        assert validation.returncode == 0, validation.stdout + validation.stderr
    assert snodo_cli(["queue", "move", "blocked", "--to", "default", "--front"]).returncode == 0
    assert snodo_cli(["queue", "move", "later", "--to", "default"]).returncode == 0
    queues_path = snodo_cli.home / ".snodo" / "queues.json"
    queues = json.loads(queues_path.read_text())
    queues["queues"]["parallel"].append("independent-work")
    queues_path.write_text(json.dumps(queues))

    run = snodo_cli(["queue", "run", "default,parallel", "--mock"])
    assert run.returncode == 1, run.stdout + run.stderr
    assert "stopped at plan 'blocked'" in run.stdout
    assert "Plan: independent-work" in run.stdout
    assert _queues(snodo_cli) == {"default": ["blocked", "later"], "parallel": []}
    status = json.loads((snodo_cli.home / ".snodo" / "plans" / "blocked" / "status.json").read_text())
    assert any(
        state.get("status") == "blocked" if isinstance(state, dict) else state == "blocked"
        for state in status["tasks"].values()
    )

    _plan(snodo_cli, "correction", "1.1_correction", "Correct the blocked plan.\n")
    validate = snodo_cli(["plan", "validate", "correction"])
    assert validate.returncode == 0, validate.stdout + validate.stderr
    assert snodo_cli(["queue", "move", "correction", "--front"]).returncode == 0
    assert _queues(snodo_cli)["default"] == ["correction", "blocked", "later"]

    # The correction uses the solo template's auto-merge path. Mark the
    # original and following fixture plans complete as the correction's
    # resulting project state, leaving the corrective plan for the runner.
    protocol = snodo_cli.home / ".snodo" / "protocol.yml"
    protocol.write_text(files("snodo.protocols.templates").joinpath("solo.yml").read_text())
    for name, task_id in (("blocked", "1.1_blocked"), ("later", "1.1_later")):
        (snodo_cli.home / ".snodo" / "plans" / name / "status.json").write_text(
            json.dumps({"tasks": {task_id: "completed"}})
        )
    resumed = snodo_cli(["queue", "run", "default", "--mock"])
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _queues(snodo_cli)["default"] == []


def test_non_blocking_keeps_failed_plan_and_runs_later_plan(snodo_cli):
    snodo_cli(["init", "--template", "2+n", "--yes"])
    _plan(snodo_cli, "blocked", "1.1_blocked", "Implement the requested feature.\n")
    _empty_plan(snodo_cli, "later")
    validation = snodo_cli(["plan", "validate", "blocked"])
    assert validation.returncode == 0, validation.stdout + validation.stderr
    queues_path = snodo_cli.home / ".snodo" / "queues.json"
    queues = json.loads(queues_path.read_text())
    queues["queues"]["default"].append("later")
    queues_path.write_text(json.dumps(queues))

    run = snodo_cli(["queue", "run", "--non-blocking", "--mock"])
    assert run.returncode == 1, run.stdout + run.stderr
    assert _queues(snodo_cli)["default"] == ["blocked"], run.stdout + run.stderr
    assert "Plan: later" in run.stdout


def test_parallel_run_without_non_blocking_runs_one_plan_at_a_time(snodo_cli):
    snodo_cli(["init", "--template", "solo", "--yes"])
    _plan(snodo_cli, "first", "1.1_first", "First task.\n")
    _empty_plan(snodo_cli, "second")
    validated = snodo_cli(["plan", "validate", "first"])
    assert validated.returncode == 0, validated.stdout + validated.stderr
    queues_path = snodo_cli.home / ".snodo" / "queues.json"
    queues = json.loads(queues_path.read_text())
    queues["queues"]["default"].append("second")
    queues_path.write_text(json.dumps(queues))

    run = snodo_cli(["queue", "run", "--parallel-run", "2", "--mock"])
    assert run.returncode == 0, run.stdout + run.stderr
    assert "requires --non-blocking; running one plan at a time" in run.stdout
    assert _queues(snodo_cli)["default"] == []
    assert run.stdout.index("Plan: first") < run.stdout.index("Plan: second")


def test_second_queue_run_is_refused_while_queue_is_locked(snodo_cli):
    from snodo.infrastructure.queue_store import QueueStore

    snodo_cli(["init", "--template", "team", "--yes"])
    store = QueueStore(snodo_cli.home)
    with store.lock("default"):
        run = snodo_cli(["queue", "run", "--mock"])
    assert run.returncode == 1
    assert "already running" in (run.stdout + run.stderr).lower()
