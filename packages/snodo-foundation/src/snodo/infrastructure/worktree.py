"""Git worktree lifecycle for parallel task isolation.

FILE: snodo/infrastructure/worktree.py

Each task gets its own git worktree (sibling to the repo, outside .git
tracking) so parallel tasks don't share filesystem state.

Worktree path:  <project_root>/../.snodo-worktrees/task_{id}/
Branch:         task/{id}/{slug}  (always off ``main``)
"""

import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

_logger = logging.getLogger(__name__)


class WorktreeIsolationError(Exception):
    """Raised when a task worktree cannot be created for a structural reason.

    Currently raised when the repository has no commits (unborn HEAD), so the
    base branch does not exist and isolation cannot be provided. This is a
    safety loss, not a transient fault — it must not degrade silently into
    running in the operator's working tree (Fixes #29).
    """


def _slugify(spec: str, max_words: int = 5) -> str:
    import re
    words = spec.strip().split()[:max_words]
    slug = "-".join(words).lower()
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug


def task_branch_name(task_id: str, spec: str) -> str:
    return f"task/{task_id}/{_slugify(spec)}"


def worktree_dir(project_root: str) -> Path:
    return Path(project_root).parent / ".snodo-worktrees"


def worktree_path(project_root: str, task_id: str) -> Path:
    return worktree_dir(project_root) / task_id


def create_worktree(
    project_root: str,
    task_id: str,
    spec: str,
    branch: Optional[str] = None,
    base: Optional[str] = None,
) -> Path:
    """Create a git worktree for *task_id*.

    Creates a branch off the resolved base branch at the worktree path.
    If the worktree already exists (retry), it is force-removed first.

    Returns:
        Absolute path to the new worktree.
    """
    from git import Repo, GitCommandError
    from snodo.tools.git import resolve_base_branch

    wt_path = worktree_path(project_root, task_id)
    branch_name = branch or task_branch_name(task_id, spec)
    base_branch = base or resolve_base_branch(project_root)

    repo = Repo(str(Path(project_root)), search_parent_directories=True)

    # A repository with no commits has an unborn HEAD: the base branch does
    # not resolve, so `git worktree add` fails with "invalid reference".
    # This is the state every greenfield repository starts in — the agent
    # would otherwise run in the operator's real working tree. Refuse
    # loudly with actionable guidance rather than degrading isolation
    # (Fixes #29). Callers that accept a degraded run must say so explicitly.
    try:
        head_commit = repo.head.commit
    except Exception:  # noqa: BLE001 — unborn HEAD raises repo-specific error types
        raise WorktreeIsolationError(
            "Cannot create a task worktree: this repository has no commits "
            "(unborn HEAD), so there is no base branch to branch from. "
            "Make an initial commit first (e.g. 'git add -A && git commit -m "
            "\"initial\"'), then re-run the task. To run without isolation, "
            "pass --no-isolation explicitly."
        )
    del head_commit  # only used to prove a resolvable HEAD

    # Remove existing worktree if present (retry / partial cleanup)
    if wt_path.exists():
        try:
            repo.git.worktree("remove", "--force", str(wt_path))
        except GitCommandError:
            shutil.rmtree(str(wt_path), ignore_errors=True)

    # Remove stale branch if present
    try:
        repo.git.branch("-D", branch_name)
    except GitCommandError:
        pass

    repo.git.worktree("add", str(wt_path), "-b", branch_name, base_branch)
    _logger.info("Created worktree %s on branch %s (off %s)", wt_path, branch_name, base_branch)
    return wt_path


def setup_for_task(
    project_root: str,
    task_id: str,
    spec: str,
    existing_worktree_path: Optional[str] = None,
) -> Optional[str]:
    """Set up a worktree for *task_id* — create if needed, return path.

    When *existing_worktree_path* is provided (set by an upstream caller
    such as ``JobManager.submit``), it is returned as-is.  Otherwise a
    fresh worktree is created.

    This is the ONE shared setup helper called by BOTH:
    - ``JobManager.submit`` (background path — sets existing before spawn)
    - ``_execute_task`` (CLI inline path — creates fresh)
    """
    if existing_worktree_path:
        return existing_worktree_path
    return str(create_worktree(project_root, task_id, spec))


def remove_worktree(project_root: str, task_id: str) -> None:
    """Remove the worktree for *task_id* (force, best-effort)."""
    wt_path = worktree_path(project_root, task_id)
    if not wt_path.exists():
        return
    try:
        from git import Repo, GitCommandError
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
        try:
            repo.git.worktree("remove", "--force", str(wt_path))
        except GitCommandError:
            shutil.rmtree(str(wt_path), ignore_errors=True)
    except Exception:
        shutil.rmtree(str(wt_path), ignore_errors=True)
    _logger.info("Removed worktree %s", wt_path)


def merge_task_branch(project_root: str, branch: str) -> Tuple[str, List[str]]:
    """Merge *branch* into the resolved base branch.

    Returns:
        Tuple of ``(status, conflicting_paths)`` where status is ``"merged"`` or
        ``"conflict"`` (the merge is aborted and the source branch/worktree are left
        intact).

    Raises:
        GitError: on any other git failure.
    """
    from snodo.tools.git import GitMCP, MergeConflictError

    git = GitMCP(project_root)
    try:
        git.merge_branch(branch)
        return "merged", []
    except MergeConflictError as e:
        return "conflict", getattr(e, "conflicting_paths", [])


def delete_task_branch(project_root: str, branch: str) -> None:
    """Delete the task branch (best-effort, after a successful merge)."""
    try:
        from git import Repo, GitCommandError
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
        try:
            repo.git.branch("-D", branch)
        except GitCommandError:
            pass
    except Exception:
        pass


def list_worktrees(project_root: str) -> list:
    """Return the retained worktree directory names, newest last.

    A retained worktree is a sibling directory under ``.snodo-worktrees/`` that
    was left behind for inspection (a task that did not complete, or one run
    with ``--retain-worktree``). Names are the task ids the worktrees were
    created for.
    """
    d = worktree_dir(project_root)
    if not d.is_dir():
        return []
    entries = [p for p in d.iterdir() if p.is_dir()]
    entries.sort(key=lambda p: p.stat().st_mtime)
    return [p.name for p in entries]
