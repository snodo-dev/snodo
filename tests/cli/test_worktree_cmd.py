"""Tests for the snodo worktree list/remove/prune commands.

FILE: tests/cli/test_worktree_cmd.py
"""

import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from snodo.infrastructure.worktree import create_worktree, worktree_path


@pytest.fixture
def git_project(tmp_path):
    # Nest the project so .snodo-worktrees (a sibling of project_root) is unique
    # per test rather than landing in the shared pytest basetemp.
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _set_age(project_root, task_id, days_old):
    p = worktree_path(project_root, task_id)
    mtime = time.time() - days_old * 86400
    os.utime(str(p), (mtime, mtime))


def _make_project(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


class TestWorktreeCommands:
    def test_shared_container_is_scoped_to_project(self, tmp_path, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command, worktree_remove_command

        project_a = _make_project(tmp_path / "project-a")
        project_b = _make_project(tmp_path / "project-b")
        create_worktree(str(project_a), "task_a", "project a")
        create_worktree(str(project_b), "task_b", "project b")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(project_a)):
            assert worktree_list_command(SimpleNamespace()) == 0
        listing = capsys.readouterr().out
        assert "task_a" in listing
        assert "task_b" not in listing

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(project_a)):
            assert worktree_remove_command(SimpleNamespace(task_id="task_b")) == 1
        assert worktree_path(project_b, "task_b").exists()

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(project_a)):
            assert worktree_remove_command(SimpleNamespace(task_id="task_a")) == 0
        assert not worktree_path(project_a, "task_a").exists()
        assert worktree_path(project_b, "task_b").exists()

    def test_list_lists_retained_worktrees(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        create_worktree(str(git_project), "task_a", "spec a")
        create_worktree(str(git_project), "task_b", "spec b")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "task_a" in out
        assert "task_b" in out
        assert "Remove a worktree with: snodo worktree remove <task_id>" in out
        assert out.count("Remove a worktree with") == 1

    def test_list_reports_merge_and_dirty_facts_in_json(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        merged_path = create_worktree(str(git_project), "merged", "merged work")
        unmerged_path = create_worktree(str(git_project), "unmerged", "unmerged work")
        (merged_path / "merged.txt").write_text("merged\n")
        subprocess.run(["git", "add", "merged.txt"], cwd=merged_path, check=True)
        subprocess.run(["git", "commit", "-qm", "merged"], cwd=merged_path, check=True)
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=merged_path, text=True
        ).strip()
        subprocess.run(
            ["git", "merge", "--no-ff", "-q", "-m", "merged", branch],
            cwd=git_project,
            check=True,
        )
        (unmerged_path / "unmerged.txt").write_text("unmerged\n")
        subprocess.run(["git", "add", "unmerged.txt"], cwd=unmerged_path, check=True)
        subprocess.run(["git", "commit", "-qm", "unmerged"], cwd=unmerged_path, check=True)
        (unmerged_path / "dirty.txt").write_text("dirty\n")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace(json=True))

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        facts = {entry["task_id"]: entry for entry in data["worktrees"]}
        assert facts["merged"]["merged"] is True
        assert facts["unmerged"]["merged"] is False
        assert facts["unmerged"]["dirty"] is True
        assert "Retained worktrees" not in output

    def test_list_pages_terminal_output_but_not_piped_output(self, git_project, monkeypatch):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        for index in range(30):
            create_worktree(str(git_project), f"task_page_{index}", "page spec")
        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(git_project)), \
             patch("snodo.cli.commands.worktree_cmd.Console") as console_class:
            console = console_class.return_value
            console.pager.return_value.__enter__.return_value = console
            monkeypatch.setattr("sys.stdout.isatty", lambda: True)
            worktree_list_command(SimpleNamespace())
            console.pager.assert_called_once_with()

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(git_project)), \
             patch("snodo.cli.commands.worktree_cmd.Console") as console_class:
            monkeypatch.setattr("sys.stdout.isatty", lambda: False)
            worktree_list_command(SimpleNamespace())
            console_class.return_value.pager.assert_not_called()

    def test_remove_deletes_worktree_and_branch(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_remove_command
        from snodo.tools.git import GitMCP

        create_worktree(str(git_project), "task_x", "spec x")
        assert worktree_path(git_project, "task_x").exists()
        git = GitMCP(str(git_project))
        assert any(h.name.startswith("task/task_x") for h in git.repo.heads)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_remove_command(SimpleNamespace(task_id="task_x"))

        assert result == 0
        assert not worktree_path(git_project, "task_x").exists()
        git = GitMCP(str(git_project))
        assert not any(h.name.startswith("task/task_x") for h in git.repo.heads)

    def test_remove_unregisters_worktree_before_deleting_branch(
        self, git_project, monkeypatch
    ):
        from snodo.cli.commands import worktree_cmd
        from snodo.infrastructure.worktree import worktree_is_owned
        from snodo.tools.git import GitMCP

        create_worktree(str(git_project), "task_order", "remove in order")
        branch = "task/task_order/remove-in-order"
        original_remove = worktree_cmd.remove_worktree
        observed = []

        def remove_then_observe(project_root, task_id):
            assert worktree_is_owned(project_root, task_id)
            assert branch in [head.name for head in GitMCP(project_root).repo.heads]
            original_remove(project_root, task_id)
            observed.append(not worktree_is_owned(project_root, task_id))

        monkeypatch.setattr(worktree_cmd, "remove_worktree", remove_then_observe)
        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            assert worktree_cmd.worktree_remove_command(
                SimpleNamespace(task_id="task_order")
            ) == 0

        assert observed == [True]
        assert branch not in [head.name for head in GitMCP(str(git_project)).repo.heads]

    def test_remove_repairs_git_registry_when_directory_is_missing(
        self, git_project, capsys
    ):
        from snodo.cli.commands.worktree_cmd import worktree_remove_command
        from snodo.infrastructure.worktree import worktree_is_owned

        create_worktree(str(git_project), "task_missing", "missing directory")
        shutil.rmtree(worktree_path(git_project, "task_missing"))
        assert worktree_is_owned(str(git_project), "task_missing")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            assert worktree_remove_command(
                SimpleNamespace(task_id="task_missing")
            ) == 0

        assert not worktree_is_owned(str(git_project), "task_missing")
        assert "Removed worktree for task_missing." in capsys.readouterr().out

    def test_remove_succeeds_when_task_branch_is_already_gone(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_remove_command

        wt = create_worktree(str(git_project), "task_detached", "branch gone")
        subprocess.run(["git", "checkout", "--detach", "-q"], cwd=wt, check=True)
        subprocess.run(
            ["git", "branch", "-D", "task/task_detached/branch-gone"],
            cwd=git_project,
            check=True,
        )

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            assert worktree_remove_command(
                SimpleNamespace(task_id="task_detached")
            ) == 0

        assert not worktree_path(git_project, "task_detached").exists()

    def test_list_filters_hidden_directories(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command
        from snodo.infrastructure.worktree import worktree_dir

        create_worktree(str(git_project), "task_valid", "spec valid")
        hidden_dir = worktree_dir(str(git_project)) / ".snodo-worktrees"
        hidden_dir.mkdir(parents=True, exist_ok=True)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "task_valid" in out
        assert ".snodo-worktrees" not in out

    def test_worktree_dir_is_not_nested_when_inside_a_container(self, git_project):
        """worktree_dir must never nest .snodo-worktrees inside itself.

        When the project root resolves to a task worktree (an agent running
        inside its own worktree), the naive ``parent/.snodo-worktrees`` produced
        ``.snodo-worktrees/.snodo-worktrees`` and made the worktree directory
        list itself. The container is reused instead (Fixes #192)."""
        from snodo.infrastructure.worktree import worktree_dir, worktree_path

        container = worktree_dir(str(git_project))
        assert container.name == ".snodo-worktrees"
        assert ".snodo-worktrees/.snodo-worktrees" not in str(container)

        inner = worktree_path(str(git_project), "task_a")
        # Resolving from inside a worktree reuses the same container, no doubling.
        assert worktree_dir(str(inner)) == container
        assert ".snodo-worktrees/.snodo-worktrees" not in str(worktree_dir(str(inner)))

    def test_list_from_inside_worktree_shows_siblings_not_a_doubled_path(
        self, git_project, capsys
    ):
        """Listing while inside a worktree shows the real siblings and never a
        ``.snodo-worktrees/.snodo-worktrees`` path (Fixes #192)."""
        from snodo.cli.commands.worktree_cmd import worktree_list_command
        from snodo.infrastructure.worktree import create_worktree, worktree_dir, worktree_path

        create_worktree(str(git_project), "task_a", "spec a")
        create_worktree(str(git_project), "task_b", "spec b")

        # A legacy nested container left behind by an earlier run: its contents
        # must never surface as retained worktrees of this project.
        (worktree_dir(str(git_project)) / ".snodo-worktrees" / "ghost").mkdir(
            parents=True, exist_ok=True
        )

        inner = str(worktree_path(str(git_project), "task_a"))
        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=inner):
            result = worktree_list_command(SimpleNamespace(json=True))

        assert result == 0
        import json
        data = json.loads(capsys.readouterr().out)
        names = {e["task_id"] for e in data["worktrees"]}
        assert {"task_a", "task_b"} <= names
        assert "ghost" not in names
        assert not any(".snodo-worktrees/.snodo-worktrees" in e["path"] for e in data["worktrees"])

    def test_prune_days_zero_refused_unguarded(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_zero", "zero spec")
        assert worktree_path(git_project, "task_zero").exists()

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=0, force=False))

        assert result == 1
        assert worktree_path(git_project, "task_zero").exists()
        err = capsys.readouterr().err
        assert "must be at least 1 day" in err

    def test_prune_skips_failure_retained_worktrees(self, git_project, capsys, monkeypatch):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_failed", "failed spec")
        _set_age(git_project, "task_failed", days_old=30)
        assert worktree_path(git_project, "task_failed").exists()

        monkeypatch.setattr(
            "snodo.cli.commands.worktree_cmd._has_failure_context",
            lambda proj, tid: tid == "task_failed",
        )

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=7, force=False))

        assert result == 0
        assert worktree_path(git_project, "task_failed").exists()
        out = capsys.readouterr().out
        assert "Skipping task_failed: retained for failure evidence" in out

    def test_prune_removes_stale_and_keeps_fresh(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_old", "old spec")
        create_worktree(str(git_project), "task_fresh", "fresh spec")
        _set_age(git_project, "task_old", days_old=30)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=7, force=True))

        assert result == 0
        assert not worktree_path(git_project, "task_old").exists()
        assert worktree_path(git_project, "task_fresh").exists()

    def test_prune_uses_protocol_branch_ttl_by_default(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_ttl_old", "older than configured ttl")
        create_worktree(str(git_project), "task_ttl_fresh", "within configured ttl")
        _set_age(git_project, "task_ttl_old", days_old=5)
        _set_age(git_project, "task_ttl_fresh", days_old=1)
        snodo_dir = git_project / ".snodo"
        snodo_dir.mkdir(exist_ok=True)
        (snodo_dir / "protocol.yml").write_text(
            "protocol_id: ttl-test\n"
            "name: TTL test\n"
            "modes:\n"
            "  - mode_id: build\n"
            "    name: Build\n"
            "    transitions: {}\n"
            "validators:\n"
            "  - validator_id: basic\n"
            "    validator_type: quality\n"
            "    evaluation_phase: post_execute\n"
            "    criteria: [passes]\n"
            "initial_mode: build\n"
            "execution:\n"
            "  branch_ttl_days: 3\n"
        )

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            assert worktree_prune_command(
                SimpleNamespace(days=None, force=True)
            ) == 0

        assert not worktree_path(git_project, "task_ttl_old").exists()
        assert worktree_path(git_project, "task_ttl_fresh").exists()

    def test_list_empty(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        assert "No retained worktrees" in capsys.readouterr().out
