"""Retained sibling checkouts must not supply another project's identity."""

from pathlib import Path

import pytest

from snodo.infrastructure import worktree
from snodo.tools.git import open_repo


@pytest.mark.parametrize("plan_name", [None, "release"])
def test_sibling_projects_keep_independent_retry_workspaces(tmp_path, monkeypatch, plan_name):
    from git import Repo

    monkeypatch.setattr(worktree, "_prepare_task_environment", lambda *args: None)
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        with Repo.init(root, initial_branch="main") as repo:
            with repo.config_writer() as config:
                config.set_value("user", "name", "Test")
                config.set_value("user", "email", "test@example.org")
            (root / "base.txt").write_text(root.name)
            repo.index.add(["base.txt"])
            repo.index.commit("base")

    first = worktree.create_worktree(str(roots[0]), "task_retry", "Add feature", plan_name=plan_name)
    (first / "feature.txt").write_text("preserved work")
    with open_repo(str(first)) as repo:
        repo.index.add(["feature.txt"])
        prior = repo.index.commit("halted work").hexsha
        branch = repo.active_branch.name

    second = worktree.create_worktree(str(roots[1]), "task_retry", "Add feature", plan_name=plan_name)
    assert second != first
    assert (second / "base.txt").read_text() == "second"
    assert not (second / "feature.txt").exists()
    assert worktree.create_worktree(
        str(roots[1]), "task_retry", "Changed spec", plan_name=plan_name,
    ) == second
    with open_repo(str(roots[1])) as repo:
        assert repo.heads[branch].commit == repo.heads.main.commit
    with open_repo(str(roots[0])) as repo:
        assert repo.heads[branch].commit.hexsha == prior
    assert (Path(first) / "feature.txt").read_text() == "preserved work"
