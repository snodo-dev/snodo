"""Regression tests for refreshing the plan integration tree from main."""

import subprocess

from snodo.cli.commands.plan_run import _sync_plan_integration


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(tmp_path, "init", "-b", "main", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "integration")
    return root


def test_sync_picks_up_main_commit_for_later_wave(tmp_path):
    root = _repo(tmp_path)
    integration = tmp_path / "integration"
    _git(root, "worktree", "add", str(integration), "integration")
    (root / "main-work.txt").write_text("from main\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "hand merged task")

    base_sha = _sync_plan_integration(str(root), str(integration), "integration")

    assert (integration / "main-work.txt").read_text() == "from main\n"
    assert base_sha == _git(integration, "rev-parse", "HEAD")


def test_sync_does_not_move_integration_when_main_is_unchanged(tmp_path):
    root = _repo(tmp_path)
    integration = tmp_path / "integration"
    _git(root, "worktree", "add", str(integration), "integration")
    before = _git(integration, "rev-parse", "HEAD")
    base_sha = _sync_plan_integration(str(root), str(integration), "integration")


    assert _git(integration, "rev-parse", "HEAD") == before
    assert base_sha == before
