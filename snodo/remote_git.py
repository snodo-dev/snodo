"""Move task commits between local and remote project clones with Git/SSH."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class RemoteGitError(RuntimeError):
    """A task commit could not be transferred or verified over SSH."""


def _git(project_root: str | Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603, S607 - git resolved from PATH by design
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RemoteGitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _ssh_remote(host: str, host_path: str) -> str:
    if not host.strip() or not host_path.strip():
        raise RemoteGitError("Remote Git host and clone path are required")
    # Git itself invokes SSH for this URL; there is no file copy or local mount.
    return f"{host.strip()}:{host_path.strip()}"


def push_base_commit(
    project_root: str | Path,
    host: str,
    host_path: str,
    base_sha: str,
    task_ref: str,
) -> str:
    """Push *base_sha* into a task-scoped ref in the host clone and return it."""
    if not base_sha:
        raise RemoteGitError(f"Base commit {base_sha!r} is not available locally")
    _git(project_root, "cat-file", "-e", f"{base_sha}^{{commit}}")
    ref_suffix = hashlib.sha256(task_ref.encode("utf-8")).hexdigest()[:24]
    remote_ref = f"refs/snodo/bases/{ref_suffix}"
    remote = _ssh_remote(host, host_path)
    result = subprocess.run(  # noqa: S603, S607 - git invokes the selected SSH transport
        ["git", "-C", str(project_root), "push", remote, f"{base_sha}:{remote_ref}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RemoteGitError(result.stderr.strip() or "Could not push task base commit over SSH")
    return remote_ref


def fetch_task_branch(
    project_root: str | Path,
    host: str,
    host_path: str,
    branch: str,
    head_sha: str,
) -> str:
    """Fetch a worker branch and require its exact commit to match *head_sha*."""
    source_ref = f"refs/heads/{branch}"
    if not branch:
        raise RemoteGitError(f"Worker reported an invalid task branch: {branch!r}")
    _git(project_root, "check-ref-format", source_ref)
    if not head_sha:
        raise RemoteGitError("Worker final record did not report a head SHA")

    ref_suffix = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:24]
    local_ref = f"refs/snodo/remote/{ref_suffix}"
    remote = _ssh_remote(host, host_path)
    result = subprocess.run(  # noqa: S603, S607 - git invokes the selected SSH transport
        ["git", "-C", str(project_root), "fetch", "--no-tags", remote, f"+{source_ref}:{local_ref}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip()
        raise RemoteGitError(
            f"Worker branch {branch!r} did not arrive over SSH"
            + (f": {detail}" if detail else "")
        )
    fetched_sha = _git(project_root, "rev-parse", "--verify", f"{local_ref}^{{commit}}")
    if fetched_sha != head_sha:
        raise RemoteGitError(
            f"Worker branch {branch!r} resolved to {fetched_sha}, not reported head {head_sha}"
        )
    # Existing merge code addresses the task by its ordinary local branch name.
    _git(project_root, "update-ref", f"refs/heads/{branch}", fetched_sha)
    return fetched_sha
