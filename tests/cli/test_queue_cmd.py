"""CLI coverage for the ADR 053 queue commands."""

import json

from typer.testing import CliRunner

from snodo.cli.main import app
from snodo.infrastructure.queue_store import QueueStore


def _plan(root, name, tasks, states=None):
    plan_dir = root / ".snodo" / "plans" / name
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "intent: test\nwaves:\n  - id: 1\n    tasks:\n"
        + "".join(f"      - {task}\n" for task in tasks)
    )
    (plan_dir / "status.json").write_text(json.dumps({"tasks": states or {}}))


def _project(tmp_path, monkeypatch):
    (tmp_path / ".snodo").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path)
    )
    return tmp_path


def test_queue_list_shows_order_and_status_from_plan_records(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    _plan(root, "first", ["1.1_one", "1.2_two"], {"1.1_one": "completed"})
    _plan(root, "second", ["1.1_three"], {"1.1_three": {"status": "blocked"}})
    store = QueueStore(root)
    store.add("first")
    store.add("second")
    store.move("second", front=True)

    result = CliRunner().invoke(app, ["queue"])
    assert result.exit_code == 0, result.output
    assert result.output.index("second [blocked]") < result.output.index("first [pending]")

    result = CliRunner().invoke(app, ["queue", "--json"])
    payload = json.loads(result.stdout)
    assert payload["schema"] == "snodo.queue.v1"
    assert payload["queues"]["default"] == [
        {"name": "second", "status": "blocked"},
        {"name": "first", "status": "pending"},
    ]


def test_queue_create_refuses_duplicate_and_json_reports_it(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    runner = CliRunner()
    created = runner.invoke(app, ["queue", "create", "build"])
    assert created.exit_code == 0, created.output
    assert "Created queue: build" in created.output

    duplicate = runner.invoke(app, ["queue", "create", "build"])
    assert duplicate.exit_code == 1
    assert "Queue already exists: build" in duplicate.stderr

    duplicate_json = runner.invoke(app, ["queue", "create", "build", "--json"])
    assert duplicate_json.exit_code == 1
    assert json.loads(duplicate_json.stdout) == {
        "schema": "snodo.queue.create.v1",
        "ok": False,
        "error": "Queue already exists: build",
    }
    assert QueueStore(root).list_queues() == {"default": [], "build": []}


def test_queue_move_supports_destination_and_positions(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    store = QueueStore(root)
    store.create_queue("other")
    for name in ("a", "b", "c"):
        store.add(name)
    runner = CliRunner()

    result = runner.invoke(app, ["queue", "move", "b", "--to", "other", "--front"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["queue", "move", "a", "--to", "other", "--before", "b", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "schema": "snodo.queue.move.v1", "ok": True, "plan": "a",
        "queue": "other", "position": "before", "anchor": "b",
    }
    assert QueueStore(root).list_queues() == {
        "default": ["c"], "other": ["a", "b"],
    }


def test_queue_move_refuses_running_plan(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    _plan(root, "busy", ["1.1_work"], {"1.1_work": "in_progress"})
    QueueStore(root).add("busy")
    result = CliRunner().invoke(app, ["queue", "move", "busy", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema"] == "snodo.queue.move.v1"
    assert payload["error"] == "Cannot move plan while it is running: busy"
    assert QueueStore(root).list_queues() == {"default": ["busy"]}
