"""Tests for the ADR 053 persistent queue store."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from snodo.infrastructure.queue_store import QueueError, QueueLockedError, QueueStore


def _plan(root: Path, name: str, tasks: list[str], states: dict | None = None) -> Path:
    plan_dir = root / ".snodo" / "plans" / name
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "intent: test\nwaves:\n  - id: 1\n    tasks:\n"
        + "".join(f"      - {task}\n" for task in tasks)
    )
    (plan_dir / "status.json").write_text(json.dumps({"tasks": states or {}}))
    return plan_dir


def test_default_queue_and_ordered_queue_operations(tmp_path):
    store = QueueStore(tmp_path)
    assert store.list_queues() == {"default": []}
    store.create_queue("later")
    store.add("first")
    store.add("second")
    store.add("third", "later")
    store.move("second", front=True)
    store.move("first", queue="later", after="third")
    assert store.list_queues() == {"default": ["second"], "later": ["third", "first"]}
    store.remove("first")
    assert store.list_queues()["later"] == ["third"]
    with pytest.raises(QueueError, match="default queue cannot be removed"):
        store.remove_queue("default")
    with pytest.raises(QueueError, match="already queued"):
        store.add("second", "later")


def test_queue_record_is_atomically_written_and_reloads(tmp_path):
    store = QueueStore(tmp_path)
    store.create_queue("build")
    store.add("plan-a", "build")
    assert json.loads((tmp_path / ".snodo" / "queues.json").read_text()) == {
        "queues": {"default": [], "build": ["plan-a"]}
    }
    assert not list((tmp_path / ".snodo").glob(".queues-*.tmp"))
    assert QueueStore(tmp_path).list_queues()["build"] == ["plan-a"]


def test_first_read_migrates_only_verified_incomplete_plans_by_creation_time(
    tmp_path, monkeypatch
):
    early = _plan(tmp_path, "early", ["1.1_a"])
    late = _plan(tmp_path, "late", ["1.1_b"])
    complete = _plan(tmp_path, "complete", ["1.1_c"], {"1.1_c": "completed"})
    invalid = _plan(tmp_path, "invalid", ["1.1_d"])
    for index, directory in enumerate((early, late, complete, invalid), start=1):
        directory.chmod(0o755)
        (directory / "plan.yml").touch()
        # Explicit mtime gives deterministic ordering on filesystems without birthtime.
        import os
        os.utime(directory, (index, index))

    def verify(plan_dir, workspace_root=None):
        return SimpleNamespace(passed=plan_dir.name != "invalid")

    monkeypatch.setattr("snodo.compiler.verifier.verify_plan_dir", verify)
    assert QueueStore(tmp_path).list_queues() == {"default": ["early", "late"]}


def test_completed_plan_is_removed_when_queue_is_read(tmp_path):
    _plan(tmp_path, "done", ["1.1_task"], {"1.1_task": {"status": "completed"}})
    store = QueueStore(tmp_path)
    store.create_queue("work")
    store.add("done", "work")
    assert store.list_queues()["work"] == []


def test_queue_lock_refuses_concurrent_runner_and_releases(tmp_path):
    store = QueueStore(tmp_path)
    with store.lock("default"):
        with pytest.raises(QueueLockedError):
            with QueueStore(tmp_path).lock("default"):
                pass
    with store.lock("default"):
        pass


def test_dead_runner_lock_file_does_not_block_queue(tmp_path):
    store = QueueStore(tmp_path)
    lock_dir = tmp_path / ".snodo" / "queue-locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "default.lock").write_text("99999999\n")
    with store.lock("default"):
        pass


def test_cross_queue_move_defaults_to_back_and_positions_validate(tmp_path):
    store = QueueStore(tmp_path)
    store.create_queue("other")
    for plan in ("a", "b", "c"):
        store.add(plan)
    store.move("b", queue="other")
    assert store.list_queues() == {"default": ["a", "c"], "other": ["b"]}
    with pytest.raises(QueueError, match="Position plan is not in queue"):
        store.move("a", queue="other", before="absent")
