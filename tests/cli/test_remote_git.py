"""Git-only remote task transfer over a fake SSH transport."""

import os
import subprocess
from pathlib import Path

import pytest

from snodo.remote_git import RemoteGitError, fetch_task_branch, push_base_commit


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Fixture")
    _git(path, "config", "user.email", "fixture@example.test")
    (path / "file.txt").write_text("base\n")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-qm", "base")
    return path


def _fake_ssh(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "ssh"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import shlex, subprocess, sys\n"
        "command = shlex.split(sys.argv[-1])\n"
        "raise SystemExit(subprocess.call(command))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_base_commit_push_and_task_branch_fetch_use_git_over_ssh(tmp_path, monkeypatch):
    _fake_ssh(tmp_path, monkeypatch)
    local = _repo(tmp_path / "local")
    host = _repo(tmp_path / "host")
    base_sha = _git(local, "rev-parse", "HEAD")

    base_ref = push_base_commit(local, "worker", str(host), base_sha, "task-1")
    assert _git(host, "rev-parse", base_ref) == base_sha

    branch = "task/task-1/fixture"
    _git(host, "checkout", "-qb", branch, base_sha)
    (host / "file.txt").write_text("task result\n")
    _git(host, "add", "file.txt")
    _git(host, "commit", "-qm", "task result")
    head_sha = _git(host, "rev-parse", "HEAD")

    assert fetch_task_branch(local, "worker", str(host), branch, head_sha) == head_sha
    assert _git(local, "rev-parse", f"refs/heads/{branch}") == head_sha
    assert _git(local, "show", f"{branch}:file.txt") == "task result"


def test_fetch_rejects_missing_or_mismatched_worker_head(tmp_path, monkeypatch):
    _fake_ssh(tmp_path, monkeypatch)
    local = _repo(tmp_path / "local")
    host = _repo(tmp_path / "host")
    branch = "task/task-1/fixture"
    _git(host, "branch", branch)

    with pytest.raises(RemoteGitError, match="not reported head"):
        fetch_task_branch(local, "worker", str(host), branch, "0" * 40)
    with pytest.raises(RemoteGitError, match="did not arrive"):
        fetch_task_branch(local, "worker", str(host), "task/missing", "0" * 40)
