"""Regression tests for refreshing the plan integration tree from main."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from snodo.cli.commands.plan_integration import refresh_plan_integration, sync_plan_integration
from snodo.cli.commands.plan_run import _execute_waves


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

    base_sha = sync_plan_integration(str(root), str(integration), "integration")

    assert (integration / "main-work.txt").read_text() == "from main\n"
    assert base_sha == _git(integration, "rev-parse", "HEAD")


def test_sync_does_not_move_integration_when_main_is_unchanged(tmp_path):
    root = _repo(tmp_path)
    integration = tmp_path / "integration"
    _git(root, "worktree", "add", str(integration), "integration")
    before = _git(integration, "rev-parse", "HEAD")
    base_sha = sync_plan_integration(str(root), str(integration), "integration")


    assert _git(integration, "rev-parse", "HEAD") == before
    assert base_sha == before


def test_sync_conflict_is_aborted_and_reported(tmp_path):
    root = _repo(tmp_path)
    integration = tmp_path / "integration"
    _git(root, "worktree", "add", str(integration), "integration")
    (integration / "base.txt").write_text("integration side\n")
    _git(integration, "commit", "-am", "integration edit")
    (root / "base.txt").write_text("main side\n")
    _git(root, "commit", "-am", "main edit")
    before = _git(integration, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="merge conflict"):
        sync_plan_integration(str(root), integration, "integration")

    assert _git(integration, "rev-parse", "HEAD") == before
    assert _git(integration, "status", "--porcelain") == ""


def test_refresh_without_integration_branch_does_nothing(tmp_path):
    with patch("snodo.cli.commands.plan_integration.sync_plan_integration") as sync:
        refresh_plan_integration(str(tmp_path), "plan", tmp_path, None)
    sync.assert_not_called()


def test_refresh_records_new_base_sha_in_plan_status(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {}}))
    with patch("snodo.infrastructure.worktree.worktree_dir", return_value=tmp_path / "wt"), \
            patch("snodo.cli.commands.plan_integration.sync_plan_integration", return_value="abc123") as sync:
        refresh_plan_integration(str(tmp_path), "plan", plan_dir, "plan/p/integration")

    assert sync.call_args.args[0] == str(tmp_path)
    assert sync.call_args.args[1].name == "integration"
    recorded = json.loads((plan_dir / "status.json").read_text())
    assert recorded["integration"] == {"branch": "plan/p/integration", "base_sha": "abc123"}
    assert recorded["tasks"] == {}


def test_waves_refresh_before_each_wave_and_stop_when_refresh_fails():
    waves = [{"id": "w1", "tasks": [], "depends_on": []}, {"id": "w2", "tasks": [], "depends_on": []}]
    calls = []
    assert _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(), "m", waves, False,
                          on_wave_start=lambda: calls.append("refresh")) is False
    assert calls == ["refresh", "refresh"]

    def refuse():
        calls.append("refused")
        raise RuntimeError("merge conflict")

    calls.clear()
    assert _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(), "m", waves, False,
                          on_wave_start=refuse) is True
    assert calls == ["refused"]
