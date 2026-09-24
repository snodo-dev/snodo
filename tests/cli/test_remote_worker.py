"""End-to-end contract tests for the stateless SSH task worker (ADR 055)."""

import io
import json
import subprocess

from snodo.cli.commands import remote_worker


def test_worker_emits_only_redacted_jsonl_and_keeps_bookkeeping_local(tmp_path, monkeypatch):
    from snodo.cli.commands import DEFAULT_PROTOCOL

    root = tmp_path / "fixture-project"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.test"], cwd=root, check=True)
    snodo_dir = root / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text(DEFAULT_PROTOCOL + "\n")
    (root / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, capture_output=True, check=True)
    task_id = "worker_fixture_task"
    secret = "worker-secret-do-not-leak"
    monkeypatch.setattr(remote_worker, "_HEARTBEAT_SECONDS", 0.005)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"WORKER_TEST_KEY": secret}) + "\n"))
    stdout = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", stdout)

    protected_paths = [
        snodo_dir / "audit.log",
        snodo_dir / "state.json",
        snodo_dir / "status.json",
        snodo_dir / "tasks",
        snodo_dir / "plans",
    ]
    before = {path: _tree_snapshot(path) for path in protected_paths}

    result = remote_worker.run_remote_task(
        project_root=str(root),
        task_id=task_id,
        spec="Add a worker fixture artifact",
        mock=True,
    )

    output_lines = stdout.getvalue().splitlines()
    records = [json.loads(line) for line in output_lines]
    assert result == 0
    assert output_lines and len(records) == len(output_lines)
    assert all(record["v"] == 1 for record in records)
    assert all(record["kind"] in {"log", "audit", "status", "heartbeat", "final"} for record in records)
    assert any(record["kind"] == "log" for record in records)
    assert any(record["kind"] == "heartbeat" for record in records)
    finals = [index for index, record in enumerate(records) if record["kind"] == "final"]
    assert finals == [len(records) - 1]
    final = records[-1]
    assert final["outcome"] == "completed"
    assert final["branch"].startswith("task/")
    assert final["head_sha"]
    assert secret not in stdout.getvalue()
    assert not any(secret in line for line in output_lines)
    assert all(_tree_snapshot(path) == snapshot for path, snapshot in before.items())

    worktree = root.parent / ".snodo-worktrees" / task_id
    assert worktree.is_dir()
    assert subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout == ""
    assert subprocess.run(
        ["git", "-C", str(worktree), "log", "-1", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == final["head_sha"]


def _tree_snapshot(path):
    """Snapshot one protected audit/status path without creating it."""
    if not path.exists():
        return None
    if path.is_file():
        return path.read_bytes()
    return {
        str(child.relative_to(path)): child.read_bytes() if child.is_file() else None
        for child in sorted(path.rglob("*"))
    }
