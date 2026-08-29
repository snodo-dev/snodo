"""Git worktree lifecycle for parallel task isolation.

FILE: snodo/infrastructure/worktree.py

Each task gets its own git worktree (sibling to the repo, outside .git
tracking) so parallel tasks don't share filesystem state.

Worktree path:  <project_root>/../.snodo-worktrees/task_{id}/
Branch:         task/{id}/{slug}  (always off ``main``)
"""

import logging
import re
import shutil
import sys
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


# Paths a task spec may legitimately name that are not files the coder should
# be able to read as authority. A spec that cites one of these is not silently
# transferring authority to the coder.
_SPEC_PATH_IGNORE = {
    ".snodo", "docs/decisions", "docs/specs", "docs/architecture",
    "CHANGELOG.md", "README.md", "CONTRIBUTING.md", "LICENSE",
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile",
    ".github", "docker", "scripts", "tests", "uv.lock",
}


_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,12}$")


def _is_path_like(token: str) -> bool:
    """Return True when *token* looks like a repository path, not prose.

    A slash-containing token is path-like when it ends in a file extension
    (``a/b/c.ext``), ends in a trailing slash (``a/b/c/``), or has at least
    three slash-separated segments (``a/b/c``). Two-segment tokens without an
    extension (``noindex/no-referrer``, ``and/or``) are prose, not paths.
    """
    if token.endswith("/"):
        return True
    if _EXTENSION_RE.search(token):
        return True
    return token.count("/") >= 2


def _spec_referenced_paths(spec: str) -> List[str]:
    """Return repository paths a task spec names, best-effort.

    A spec that cites a path snodo cannot see is a spec whose authority is
    silently transferred to the coder: the coder writes its own version of the
    file and the validators then judge the work against the document the coder
    just authored (issue #93). This extracts candidate paths from the spec text
    so the caller can check they exist in the worktree before dispatch.

    A token is treated as a cited path only when it is path-like, not merely
    slash-containing: it must end in a file extension (``a/b/c.ext``), end in
    a trailing slash (``a/b/c/``), or have at least three slash-separated
    segments (``a/b/c``). Slash-containing prose such as ``noindex/no-referrer``
    or ``and/or`` is not a path and is not flagged — a guard that cries wolf
    gets ignored, and this one guards against a failure that already cost a
    whole task once (issue #99). The trade-off is deliberate: a two-segment
    extensionless path written in prose (``src/parser``) is now missed, and a
    path named without a path-like token was already missed. Paths in the
    ignore set are never returned.
    """
    found: List[str] = []
    for token in re.findall(r"[A-Za-z0-9_./-]+", spec):
        token = token.strip("/")
        if not token or token.startswith(".") or "/" not in token:
            continue
        if not _is_path_like(token):
            continue
        # Ignore governance/authority paths the coder must not read, and any
        # path under them (prefix match), so a spec citing docs/decisions/0001
        # is not flagged.
        if any(
            token == ig or token.startswith(ig + "/")
            for ig in _SPEC_PATH_IGNORE
        ):
            continue
        if token not in found:
            found.append(token)
    return found


def check_spec_paths_exist(
    project_root: str,
    spec: str,
    worktree: Optional[str] = None,
) -> List[str]:
    """Return spec-referenced paths that do NOT exist in the worktree.

    *worktree* is the task worktree (built from the branch); when None, the
    project root is checked. A path that exists in the operator's working tree
    but is untracked is absent from the worktree — the coder would invent it
    (issue #93). This is a warning, not a halt: specs legitimately name paths
    that are meant to be created, and only the operator can tell the two apart.
    """
    base = Path(worktree) if worktree else Path(project_root)
    missing = []
    for rel in _spec_referenced_paths(spec):
        if not (base / rel).exists():
            missing.append(rel)
    return missing


def surface_untracked_files(project_root: str) -> List[str]:
    """Return untracked files in the project root that a worktree will not see.

    A task worktree is built from the branch, so an untracked file the operator
    can see in their working tree is absent from the worktree. If a task spec
    cites such a file, the coder invents it and the validators judge the work
    against the coder's own document (issue #93). Surfacing the untracked set
    at worktree creation makes "the operator can see it and snodo cannot" a
    visible fact instead of a silent gap.
    """
    try:
        from git import Repo
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
        return sorted(repo.untracked_files)
    except Exception:  # noqa: BLE001 — best-effort; never block on this
        return []


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
    except Exception as e:  # noqa: BLE001 — unborn HEAD raises repo-specific error types
        raise WorktreeIsolationError(
            "Cannot create a task worktree: this repository has no commits "
            "(unborn HEAD), so there is no base branch to branch from. "
            "Make an initial commit first (e.g. 'git add -A && git commit -m "
            "\"initial\"'), then re-run the task. To run without isolation, "
            "pass --no-isolation explicitly."
        ) from e
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

    # Surface untracked files in the project root: a task worktree is built
    # from the branch, so an untracked file the operator can see is absent
    # from the worktree. If the task spec cites such a file, the coder invents
    # it and the validators judge the work against the coder's own document
    # (issue #93). This makes "the operator can see it and snodo cannot" a
    # visible fact at the moment the worktree is created.
    untracked = surface_untracked_files(project_root)
    if untracked:
        print(
            "  Note: the following untracked files exist in your working tree "
            "but are NOT in the task worktree (built from the branch):",
            file=sys.stderr,
        )
        for path in untracked:
            print(f"    - {path}", file=sys.stderr)
        print(
            "  If the task spec cites one of these as authority, the coder "
            "cannot see it and will invent its own version. Commit the file "
            "or reference it differently.",
            file=sys.stderr,
        )

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


def merge_head_sha(project_root: str) -> str:
    """Return the base branch HEAD commit SHA after a merge, or "" if unreadable.

    The merge commit is the natural identity of a merged unit: N merges of the
    same worktree branch produce N distinct merge commits, so the audit record
    can tell them apart (Fixes #101). The branch name is a human-readable
    label, not an identity — it repeats across merges.
    """
    try:
        from git import Repo
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
        return repo.head.commit.hexsha
    except Exception:  # noqa: BLE001 — best-effort; never block on this
        return ""


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
