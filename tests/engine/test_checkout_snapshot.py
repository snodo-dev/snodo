"""Real-repository tests for primary checkout content snapshots."""

import subprocess
from pathlib import Path

from snodo.engine.checkout_snapshot import compare_checkout_snapshot, take_checkout_snapshot


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "tracked").write_text("base")
    git(root, "add", "tracked")
    git(root, "commit", "-m", "initial")
    return root


def changes(root: Path, baseline) -> tuple[str, ...]:
    result = compare_checkout_snapshot(baseline)
    assert result.available, result.reason
    return result.changed_paths


def test_clean_tree_unchanged(tmp_path):
    root = repo(tmp_path)
    baseline = take_checkout_snapshot(root).snapshot
    assert baseline is not None
    assert changes(root, baseline) == ()


def test_tracked_dirty_and_deleted_changes(tmp_path):
    root = repo(tmp_path)
    baseline = take_checkout_snapshot(root).snapshot
    assert baseline
    (root / "tracked").write_text("one")
    assert changes(root, baseline) == ("tracked",)
    (root / "tracked").write_text("two")
    assert changes(root, baseline) == ("tracked",)
    (root / "tracked").unlink()
    assert changes(root, baseline) == ("tracked",)


def test_untracked_creation_and_removal(tmp_path):
    root = repo(tmp_path)
    baseline = take_checkout_snapshot(root).snapshot
    assert baseline
    file = root / "new.txt"
    file.write_text("new")
    assert changes(root, baseline) == ("new.txt",)
    file.unlink()
    assert changes(root, baseline) == ()


def test_ignored_and_snodo_runtime_excluded_but_plan_is_not(tmp_path):
    root = repo(tmp_path)
    (root / ".gitignore").write_text("ignored\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore")
    baseline = take_checkout_snapshot(root).snapshot
    assert baseline
    (root / "ignored").write_text("x")
    (root / ".snodo").mkdir()
    (root / ".snodo" / "audit.jsonl").write_text("runtime")
    (root / "plan.md").write_text("plan")
    assert changes(root, baseline) == ("plan.md",)


def test_git_unavailable_is_explicit(tmp_path, monkeypatch):
    root = repo(tmp_path)
    monkeypatch.setenv("PATH", "")
    result = take_checkout_snapshot(root)
    assert not result.available
    assert result.reason
