"""Tests for git worktree lifecycle and task-branch merge.

FILE: tests/infrastructure/test_worktree.py
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from snodo.infrastructure.worktree import (
    WorktreeIsolationError,
    check_spec_paths_exist,
    create_worktree,
    delete_task_branch,
    merge_task_branch,
    surface_untracked_files,
    task_branch_name,
)
from snodo.tools.git import GitError, resolve_base_branch


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _current_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _branches(root: Path) -> str:
    return subprocess.run(
        ["git", "branch"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        yield root


# === resolve_base_branch ===

def test_resolve_base_branch_defaults_to_main(repo):
    assert resolve_base_branch(str(repo)) == "main"


def test_resolve_base_branch_uses_remote_head(repo):
    # Simulate a repository whose remote default is not main.
    subprocess.run(["git", "init", "-qb", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    assert resolve_base_branch(str(repo)) == "master"


# === unborn HEAD (no commits) — fail loud, never degrade to no isolation ===

def _init_unborn_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_create_worktree_on_unborn_head_raises(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        create_worktree(str(root), "task_1", "do the thing")


def test_setup_for_task_on_unborn_head_propagates(tmp_path):
    """setup_for_task must not swallow the isolation failure and return None.

    Returning None is the silent-degradation path: the caller would run the
    agent in the operator's working tree. The structural error must surface
    so a human can decide (Fixes #29).
    """
    from snodo.infrastructure.worktree import setup_for_task

    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        setup_for_task(str(root), "task_1", "do the thing")


# === create_worktree branches off the resolved base ===

def test_create_worktree_branches_off_non_main_base(repo):
    # Make the default branch "master" (rename main -> master).
    subprocess.run(["git", "branch", "-m", "main", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    # Add a commit on master so we can tell the worktree branched from it.
    (repo / "base_only.txt").write_text("base\n")
    subprocess.run(["git", "add", "base_only.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base commit"], cwd=repo, check=True)

    wt = create_worktree(str(repo), "task_1", "do the thing")

    assert wt.exists()
    assert (wt / "base_only.txt").exists()  # inherited from master, not main


# === merge_task_branch ===

def test_merge_task_branch_success(repo):
    branch = task_branch_name("task_1", "add feature")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    assert merge_task_branch(str(repo), branch) == ("merged", [])

    assert _current_branch(repo) == "main"
    assert (repo / "feature.txt").exists()  # base branch now has the commit


def test_merge_task_branch_conflict(repo):
    branch = task_branch_name("task_1", "conflicting change")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "README.md").write_text("branch content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch change"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("main content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "main change"], cwd=repo, check=True)

    status, paths = merge_task_branch(str(repo), branch)
    assert status == "conflict"
    assert paths == ["README.md"]

    # Base branch is left clean (merge aborted) and the source branch survives.
    assert _current_branch(repo) == "main"
    assert "README.md" not in _current_branch(repo)  # sanity: no conflict markers path
    assert branch in _branches(repo)


def test_merge_task_branch_nonexistent_raises(repo):
    with pytest.raises(GitError):
        merge_task_branch(str(repo), "task/nope/missing")


# === delete_task_branch ===

def test_delete_task_branch(repo):
    branch = task_branch_name("task_1", "delete me")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    delete_task_branch(str(repo), branch)
    assert branch not in _branches(repo)


# === spec-referenced paths must exist in the worktree (issue #93) ===

def test_check_spec_paths_exist_flags_missing_cited_file(repo):
    """A spec citing a path absent from the worktree is flagged before dispatch."""
    missing = check_spec_paths_exist(
        str(repo),
        "Implement the card footer per docs/design/card-footer-qr.html",
    )
    assert "docs/design/card-footer-qr.html" in missing


def test_check_spec_paths_exist_flags_paths_meant_to_be_created(repo):
    """A spec naming a path the task is meant to create IS flagged — that is
    the false positive the warning tolerates. The check warns rather than
    halts precisely because only the operator can tell 'to be created' from
    'cited as authority'."""
    missing = check_spec_paths_exist(
        str(repo),
        "Create src/parser.py and tests/test_parser.py",
    )
    assert "src/parser.py" in missing
    # tests/ is governance/authority-adjacent and ignored.
    assert "tests/test_parser.py" not in missing


def test_check_spec_paths_exist_ignores_governance_paths(repo):
    """Specs citing docs/decisions or .snodo are not flagged — the coder must
    not read those as authority anyway."""
    missing = check_spec_paths_exist(
        str(repo),
        "Follow docs/decisions/0001 and the .snodo protocol",
    )
    assert missing == []


def test_check_spec_paths_exist_flags_real_path_but_not_slash_prose(repo):
    """A real cited path is flagged; slash-containing prose is not (Fixes #99).

    The detector must not match anything containing a slash: on its first real
    run it flagged ``noindex/no-referrer``, which is prose in a sentence, not a
    filename. A guard that cries wolf gets ignored, and this one guards
    against a failure that already cost a whole task once.
    """
    missing = check_spec_paths_exist(
        str(repo),
        "Add rel=noindex/no-referrer to the card footer per "
        "docs/design/card-footer-qr.html",
    )
    assert "docs/design/card-footer-qr.html" in missing
    assert "noindex/no-referrer" not in missing


def test_check_spec_paths_exist_against_worktree(repo, tmp_path):
    """A file present in the project root but absent from the worktree is
    flagged when the worktree is checked — the untracked-file gap."""
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "design").mkdir(exist_ok=True)
    (repo / "docs" / "design" / "card-footer-qr.html").write_text("<html>ref</html>")
    # Untracked: exists in the operator's tree, absent from any worktree.
    # Simulate the worktree as a separate directory that does not have it.
    fake_worktree = tmp_path / "wt"
    fake_worktree.mkdir()
    missing = check_spec_paths_exist(
        str(repo),
        "Implement per docs/design/card-footer-qr.html",
        worktree=str(fake_worktree),
    )
    assert "docs/design/card-footer-qr.html" in missing


def test_surface_untracked_files_lists_untracked(repo):
    (repo / "untracked.txt").write_text("new")
    (repo / "tracked.txt").write_text("tracked")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "tracked"], cwd=repo, check=True)

    untracked = surface_untracked_files(str(repo))
    assert "untracked.txt" in untracked
    assert "tracked.txt" not in untracked
