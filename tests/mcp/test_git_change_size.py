"""Tests for the change-size statistic in the git tooling (Fixes #377).

FILE: tests/mcp/test_git_change_size.py

``GitMCP.change_size`` answers "how much changed between two refs" with
counts only — never the diff text — bounded so a wide change cannot stall
a run. These cover: the countable shapes with their exact known totals,
the shapes whose lines cannot be counted (so a zero never silently means
both "no lines" and "not countable"), and the cap past which line totals
are reported as not-measured rather than computed.
"""

import os
import subprocess
from pathlib import Path

import pytest

from snodo.tools.git import CHANGE_SIZE_MAX_FILES, GitError, GitMCP


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A git repo on ``main`` with one committed file, plus a task branch."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    # Mode-only changes must be visible to git here; a global
    # core.fileMode=false would silently hide them.
    _git(tmp_path, "config", "core.fileMode", "true")
    (tmp_path / "keep.txt").write_text("one\ntwo\nthree\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "task/t1/change-size")
    return tmp_path


def _commit_all(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


def _stat(root: Path, base: str, head: str, **kwargs):
    git = GitMCP(str(root))
    base_sha = git.repo.commit(base).hexsha
    head_sha = git.repo.commit(head).hexsha
    return git.change_size(base_sha, head_sha, **kwargs)


# ========== countable shapes: exact totals against a known diff ==========

def test_text_edit_reports_known_line_totals(repo):
    (repo / "keep.txt").write_text("one\nTWO\nthree\nfour\n")
    (repo / "new.txt").write_text("a\nb\n")
    _commit_all(repo, "edit + add")

    stat = _stat(repo, "main", "task/t1/change-size")

    assert stat["lines_added"] == 4      # TWO, four, a, b
    assert stat["lines_deleted"] == 1    # two
    assert stat["files_changed"] == 2
    assert stat["paths"] == ["keep.txt", "new.txt"]
    assert stat["files_added"] == 1
    assert stat["capped"] is False
    assert stat["base_sha"] == GitMCP(str(repo)).repo.commit("main").hexsha


def test_deletion_lines_land_in_deletions_not_additions(repo):
    os.remove(repo / "keep.txt")
    _commit_all(repo, "delete")

    stat = _stat(repo, "main", "task/t1/change-size")

    assert stat["lines_deleted"] == 3
    assert stat["lines_added"] == 0
    assert stat["files_deleted"] == 1


def test_pure_rename_counts_once_and_moves_no_lines(repo):
    os.rename(repo / "keep.txt", repo / "moved.txt")
    _commit_all(repo, "rename")

    stat = _stat(repo, "main", "task/t1/change-size")

    assert stat["files_changed"] == 1
    assert stat["files_renamed"] == 1
    assert stat["paths"] == ["moved.txt"]
    assert stat["lines_added"] == 0
    assert stat["lines_deleted"] == 0
    assert stat["files_binary"] == 0


def test_mode_only_change_is_visible_without_line_movement(repo):
    os.chmod(repo / "keep.txt", 0o755)
    _commit_all(repo, "mode only")

    stat = _stat(repo, "main", "task/t1/change-size")

    assert stat["files_changed"] == 1
    assert stat["files_mode_only"] == 1
    assert stat["lines_added"] == 0
    assert stat["lines_deleted"] == 0


# ========== uncountable shapes ==========

def test_binary_change_has_no_countable_lines(repo):
    (repo / "blob.dat").write_bytes(b"\x00\x01\x02binary\xff")
    _commit_all(repo, "binary add")

    stat = _stat(repo, "main", "task/t1/change-size")

    assert stat["files_binary"] == 1
    # The zeros here mean "no lines", and ``files_binary`` is what says the
    # file's lines were never countable at all — a "-" is not folded to 0.
    assert stat["lines_added"] == 0
    assert stat["lines_deleted"] == 0
    assert stat["files_changed"] == 1


def test_binary_only_change_is_distinguishable_from_no_change(repo):
    (repo / "blob.dat").write_bytes(b"\x00\x01\x02\xff")
    _commit_all(repo, "binary only")

    stat = _stat(repo, "main", "task/t1/change-size")

    empty = _stat(repo, "main", "main")
    assert (stat["lines_added"], stat["lines_deleted"]) == (
        empty["lines_added"],
        empty["lines_deleted"],
    ) == (0, 0)
    # Same-looking zeros; the record keeps them apart via the counts.
    assert stat["files_changed"] == 1 and stat["files_binary"] == 1
    assert empty["files_changed"] == 0 and empty["files_binary"] == 0


# ========== the cost bound ==========

def test_past_the_bound_line_totals_are_not_measured(repo):
    for i in range(4):
        (repo / f"f{i}.txt").write_text("a\nb\nc\n")
    _commit_all(repo, "several files")

    stat = _stat(repo, "main", "task/t1/change-size", max_files=3)

    assert stat["capped"] is True
    assert stat["files_changed"] == 4      # the real count, always
    assert stat["lines_added"] is None     # null, not a fabricated zero
    assert stat["lines_deleted"] is None
    assert stat["files_binary"] is None
    assert stat["paths"] == ["f0.txt", "f1.txt", "f2.txt"]
    assert len(stat["paths"]) < stat["files_changed"]


def test_default_bound_is_a_real_bound(repo):
    assert CHANGE_SIZE_MAX_FILES > 0
    # The default must be far above any repository the tests build.
    assert CHANGE_SIZE_MAX_FILES >= 100


def test_capped_run_skips_the_content_comparison(repo, monkeypatch):
    for i in range(3):
        (repo / f"f{i}.txt").write_text("x\n")
    _commit_all(repo, "three files")

    calls = []
    git = GitMCP(str(repo))
    real_git = git.repo.git

    class _SpyGit:
        """Records diff invocations, delegating to the real Git object.

        ``Git`` is slotted (no instance attributes), so the spy replaces
        ``repo.git`` wholesale rather than patching a method on it.
        """

        def diff(self, *args, **kwargs):
            calls.append(" ".join(str(a) for a in args))
            return real_git.diff(*args, **kwargs)

    monkeypatch.setattr(git.repo, "git", _SpyGit())
    base = real_git.rev_parse("main").strip()
    head = real_git.rev_parse("task/t1/change-size").strip()

    stat = git.change_size(base, head, max_files=2)

    assert stat["capped"] is True
    # Only the cheap name-only walk ran; no numstat/name-status content
    # comparison — the bound is what the cap is *for*.
    assert any("--name-only" in c for c in calls)
    assert not any("--numstat" in c or "--name-status" in c for c in calls)


# ========== errors ==========

def test_unresolvable_ref_raises_git_error(repo):
    git = GitMCP(str(repo))
    with pytest.raises(GitError, match="resolve refs"):
        git.change_size("no-such-ref", "HEAD")
