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
from typing import Optional

_logger = logging.getLogger(__name__)


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
    return worktree_dir(project_root) / f"task_{task_id}"


def create_worktree(
    project_root: str,
    task_id: str,
    spec: str,
    branch: Optional[str] = None,
) -> Path:
    """Create a git worktree for *task_id*.

    Creates a branch off ``main`` at the worktree path.
    If the worktree already exists (retry), it is force-removed first.

    Returns:
        Absolute path to the new worktree.
    """
    from git import Repo, GitCommandError

    wt_path = worktree_path(project_root, task_id)
    branch_name = branch or task_branch_name(task_id, spec)

    repo = Repo(str(Path(project_root)), search_parent_directories=True)

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

    repo.git.worktree("add", str(wt_path), branch_name, "main")
    _logger.info("Created worktree %s on branch %s", wt_path, branch_name)
    return wt_path


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
