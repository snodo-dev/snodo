"""Tests for queue runner orchestration (ADR 053)."""

import json
import threading
from types import SimpleNamespace

from snodo.cli.commands.queue_run_cmd import _queue_run, _run_queue
from snodo.infrastructure.queue_store import QueueStore


def test_runs_default_and_removes_completed_plans(tmp_path):
    store = QueueStore(tmp_path)
    store.add("first")
    store.add("second")
    calls = []

    def run(args):
        calls.append(args.plan)
        return 0

    assert _run_queue(store, "default", tmp_path, run, SimpleNamespace,
                      ".snodo/protocol.yml", True, False, 1) == 0
    assert calls == ["first", "second"]
    assert store.list_queues()["default"] == []


def test_blocking_failure_reports_task_and_does_not_skip(tmp_path, capsys):
    store = QueueStore(tmp_path)
    store.add("stuck")
    store.add("later")
    plan_dir = tmp_path / ".snodo" / "plans" / "stuck"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "waves:\n  - tasks:\n      - 1.1_build\n"
    )
    (plan_dir / "status.json").write_text(json.dumps({
        "tasks": {"1.1_build": {"status": "blocked", "reason": "needs input"}},
    }))
    calls = []

    assert _run_queue(
        store, "default", tmp_path,
        lambda args: calls.append(args.plan) or 1,
        SimpleNamespace, ".snodo/protocol.yml", False, False, 1,
    ) == 1
    assert calls == ["stuck"]
    output = capsys.readouterr().out
    assert "stuck" in output
    assert "1.1_build" in output
    assert "needs input" in output


def test_non_blocking_keeps_failed_plan_and_runs_next(tmp_path):
    store = QueueStore(tmp_path)
    store.add("stuck")
    store.add("later")
    (tmp_path / ".snodo" / "plans" / "stuck").mkdir(parents=True)
    (tmp_path / ".snodo" / "plans" / "stuck" / "plan.yml").write_text(
        "waves:\n  - tasks:\n      - 1.1_build\n"
    )
    (tmp_path / ".snodo" / "plans" / "stuck" / "status.json").write_text(
        json.dumps({"tasks": {"1.1_build": "errored"}})
    )
    calls = []

    def run(args):
        calls.append(args.plan)
        return int(args.plan == "stuck")

    assert _run_queue(
        store, "default", tmp_path, run, SimpleNamespace,
        ".snodo/protocol.yml", False, True, 1,
    ) == 1
    assert calls == ["stuck", "later"]
    assert store.list_queues()["default"] == ["stuck"]


def test_does_not_start_plan_with_active_task_status(tmp_path):
    store = QueueStore(tmp_path)
    store.add("active")
    plan_dir = tmp_path / ".snodo" / "plans" / "active"
    plan_dir.mkdir(parents=True)
    (plan_dir / "status.json").write_text(
        json.dumps({"tasks": {"1.1_build": {"status": "in_progress"}}})
    )
    calls = []

    assert _run_queue(
        store, "default", tmp_path,
        lambda args: calls.append(args.plan) or 0,
        SimpleNamespace, ".snodo/protocol.yml", False, False, 1,
    ) == 1
    assert calls == []


def test_non_blocking_parallel_runs_multiple_plans_at_once(tmp_path):
    store = QueueStore(tmp_path)
    store.add("first")
    store.add("second")
    barrier = threading.Barrier(2)
    calls = []

    def run(args):
        calls.append(args.plan)
        barrier.wait(timeout=5)
        return 0

    assert _run_queue(
        store, "default", tmp_path, run, SimpleNamespace,
        ".snodo/protocol.yml", False, True, 2,
    ) == 0
    assert sorted(calls) == ["first", "second"]
    assert store.list_queues()["default"] == []


def test_all_runs_queues_in_creation_order_and_reads_defaults(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    store.create_queue("later")
    store.add("default-plan")
    store.add("later-plan", "later")
    monkeypatch.setattr("snodo.cli.commands.queue_run_cmd.require_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "snodo.cli.commands.load_protocol",
        lambda _: SimpleNamespace(queue=SimpleNamespace(non_blocking=False, parallel_runs=1)),
    )
    calls = []
    monkeypatch.setattr(
        "snodo.cli.commands.plan_run._run_plan",
        lambda args: calls.append(args.plan) or 0,
    )

    assert _queue_run(all_queues=True) == 0
    assert calls == ["default-plan", "later-plan"]


def test_queue_runner_arms_and_disarms_liveness_on_failure(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    store.add("blocked")
    monkeypatch.setattr("snodo.cli.commands.queue_run_cmd.require_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "snodo.cli.commands.load_protocol",
        lambda _: SimpleNamespace(queue=SimpleNamespace(non_blocking=False, parallel_runs=1)),
    )
    monkeypatch.setattr("snodo.cli.commands.plan_run._run_plan", lambda _: 1)
    calls = []
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.install", lambda: calls.append("install"))
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.uninstall", lambda: calls.append("uninstall"))

    assert _queue_run() == 1
    assert calls == ["install", "uninstall"]


def test_named_queues_run_in_parallel(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    store.create_queue("other")
    store.add("one", "default")
    store.add("two", "other")
    monkeypatch.setattr("snodo.cli.commands.queue_run_cmd.require_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "snodo.cli.commands.load_protocol",
        lambda _: SimpleNamespace(queue=SimpleNamespace(non_blocking=False, parallel_runs=1)),
    )
    barrier = threading.Barrier(2)
    calls = []

    def run(args):
        calls.append(args.plan)
        barrier.wait(timeout=5)
        return 0

    monkeypatch.setattr("snodo.cli.commands.plan_run._run_plan", run)
    assert _queue_run(queues="default,other") == 0
    assert sorted(calls) == ["one", "two"]
