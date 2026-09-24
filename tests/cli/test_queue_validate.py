"""Tests for the read-only queue validation report."""

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from snodo.cli.commands.queue_validate_cmd import build_validation_report
from snodo.cli.main import app
from snodo.infrastructure.queue_store import QueueStore


def _plan(root: Path, name: str, spec: str, status: dict | None = None) -> None:
    plan_dir = root / ".snodo" / "plans" / name
    (plan_dir / "wave_1").mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "intent: test\nwaves:\n  - id: 1\n    tasks:\n      - 1.1_task\n"
    )
    (plan_dir / "wave_1" / "1.1_task_task.md").write_text(spec)
    if status is not None:
        (plan_dir / "status.json").write_text(json.dumps({"tasks": status}))


def test_validate_reports_verification_order_collisions_and_live_locks(tmp_path, monkeypatch):
    snodo = tmp_path / ".snodo"
    snodo.mkdir()
    _plan(tmp_path, "consumer", "Read src/shared.py and src/foreign.py")
    _plan(tmp_path, "producer", "Create new file src/shared.py")
    _plan(tmp_path, "other-consumer", "Read src/shared.py")
    _plan(tmp_path, "foreign-producer", "Create new file src/foreign.py")
    (snodo / "queues.json").write_text(json.dumps({"queues": {
        "default": ["consumer", "producer"], "other": ["other-consumer", "foreign-producer"]
    }}))
    monkeypatch.setattr(
        "snodo.compiler.verifier.verify_plan_dir",
        lambda plan_dir, workspace_root=None: SimpleNamespace(
            passed=plan_dir.name not in {"producer", "foreign-producer"},
            errors=["stale path"] if plan_dir.name in {"producer", "foreign-producer"} else [],
        ),
    )

    store = QueueStore(tmp_path)
    with store.lock("default"):
        report = build_validation_report(tmp_path)

    assert report["queues"]["default"]["runnable"]
    assert report["queues"]["default"]["runner_active"]
    assert report["queues"]["other"]["runner_active"] is False
    assert report["queues"]["default"]["plans"][1]["verification_errors"] == ["stale path"]
    assert report["queues"]["default"]["order_problems"] == [
        {"type": "later_plan_creates_cited_path", "plan": "consumer", "path": "src/shared.py"},
        {"type": "other_queue_creates_cited_path", "plan": "consumer", "path": "src/foreign.py"},
    ]
    assert report["cross_queue_warnings"] == [
        {"queues": ["default", "other"], "path": "src/foreign.py"},
        {"queues": ["default", "other"], "path": "src/shared.py"},
    ]


def test_validate_reports_stopped_task_and_does_not_modify_queue_files(tmp_path, monkeypatch):
    snodo = tmp_path / ".snodo"
    snodo.mkdir()
    _plan(tmp_path, "blocked", "Implement it", {
        "1.1_task": {"status": "blocked", "reason": "needs API decision"}
    })
    record = snodo / "queues.json"
    record.write_text(json.dumps({"queues": {"default": ["blocked"]}}))
    monkeypatch.setattr(
        "snodo.compiler.verifier.verify_plan_dir",
        lambda plan_dir, workspace_root=None: SimpleNamespace(passed=True, errors=[]),
    )
    before = record.read_bytes()

    report = build_validation_report(tmp_path)

    assert report["queues"]["default"]["runnable"] is False
    assert report["queues"]["default"]["front"]["stopped_by"] == {
        "task": "1.1_task", "status": "blocked", "reason": "needs API decision"
    }
    assert record.read_bytes() == before
    assert not (snodo / ".queues.lock").exists()
    assert not (snodo / "queue-locks" / "default.lock").exists()


def test_queue_validate_is_registered_and_supports_json(tmp_path, monkeypatch):
    snodo = tmp_path / ".snodo"
    snodo.mkdir()
    (snodo / "queues.json").write_text(json.dumps({"queues": {"default": []}}))
    monkeypatch.setattr("snodo.cli.commands.queue_validate_cmd.require_project_root", lambda: tmp_path)

    result = CliRunner().invoke(app, ["queue", "validate", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema"] == "snodo.queue_validate.v1"
    assert payload["queues"]["default"]["front"] is None
