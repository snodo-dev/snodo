"""Git worktree lifecycle for parallel task isolation.

FILE: snodo/infrastructure/worktree.py

Each task gets its own git worktree (sibling to the repo, outside .git
tracking) so parallel tasks don't share filesystem state.

Worktree path:  <project_root>/../.snodo-worktrees/<plan>/<task_id>/
Branch:         task/<plan>/<task_id>/<slug>  (always off ``main``)

When no plan is supplied, the original task-only names are retained. This is
important for standalone tasks and for tasks already in flight under the old
naming scheme.
"""

import logging
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import List, Optional, Set, Tuple

import yaml

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


def _name_component(value: str) -> str:
    """Make a plan name safe for both a Git ref component and a directory."""
    component = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip(".-")
    return component or "plan"


def task_plan_name(args) -> Optional[str]:
    """Return the plan scope carried by inline or background task arguments."""
    import os
    return getattr(args, "plan", None) or os.environ.get("SNODO_TASK_PLAN")


def legacy_task_branch_name(task_id: str, spec: str) -> str:
    """Return the pre-plan-scoped branch name."""
    return f"task/{task_id}/{_slugify(spec)}"


def task_branch_name(task_id: str, spec: str, plan_name: Optional[str] = None) -> str:
    """Return the branch name for a task, scoped when it belongs to a plan."""
    if plan_name:
        return f"task/{_name_component(plan_name)}/{task_id}/{_slugify(spec)}"
    return legacy_task_branch_name(task_id, spec)


_WORKTREE_CONTAINER = ".snodo-worktrees"


def worktree_dir(project_root: str) -> Path:
    """Return the container that holds this project's task worktrees.

    Worktrees live in a ``.snodo-worktrees`` directory that is a *sibling* of
    the project root. When *project_root* already sits inside such a container
    — which happens when the project root resolves to a task worktree, e.g. an
    agent running inside its own worktree — reuse that container instead of
    nesting another one. The blind ``parent / ".snodo-worktrees"`` produced
    ``.snodo-worktrees/.snodo-worktrees`` and made the worktree directory list
    itself (Fixes #192).
    """
    p = Path(project_root)
    for ancestor in (p, *p.parents):
        if ancestor.name == _WORKTREE_CONTAINER:
            return ancestor
    return p.parent / _WORKTREE_CONTAINER


def worktree_path(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> Path:
    if plan_name:
        return worktree_dir(project_root) / _name_component(plan_name) / task_id
    return worktree_dir(project_root) / task_id


def _branch_exists(project_root: str, branch: str) -> bool:
    try:
        from snodo.tools.git import open_repo
        with open_repo(project_root) as repo:
            return branch in repo.heads
    except Exception:
        return False


def _task_identity(
    project_root: str, task_id: str, spec: str, plan_name: Optional[str]
) -> Tuple[Path, str]:
    """Select the new identity, falling back to an existing legacy identity."""
    new_path = worktree_path(project_root, task_id, plan_name)
    new_branch = task_branch_name(task_id, spec, plan_name)
    if not plan_name:
        return new_path, new_branch

    old_path = worktree_path(project_root, task_id)
    old_branch = legacy_task_branch_name(task_id, spec)
    if old_path.exists() or _branch_exists(project_root, old_branch):
        return old_path, old_branch
    return new_path, new_branch


def _project_worktree_paths(project_root: str) -> set[Path]:
    """Return worktree paths Git records as belonging to this repository."""
    try:
        from snodo.tools.git import open_repo

        with open_repo(project_root) as repo:
            raw = repo.git.worktree("list", "--porcelain")
    except Exception as e:
        _logger.debug("Could not inspect repository worktrees: %s", e)
        return set()

    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in raw.splitlines()
        if line.startswith("worktree ")
    }


def worktree_is_owned(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> bool:
    """Return whether Git records *task_id* as a worktree of this repository."""
    path, _ = _task_identity(project_root, task_id, "", plan_name)
    return path.resolve() in _project_worktree_paths(project_root)


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
    for token in re.findall(r"[A-Za-z0-9_./-]+", _spec_citation_text(spec)):
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


def _spec_citation_text(spec: str) -> str:
    """Return spec prose with quoted evidence removed.

    Specs are evidence-first documents, so fenced command output, JSON, and
    log lines are not repository citations.  Inline quoted output is treated
    the same way.  Ordinary inline-code citations remain available because
    backticks are deliberately not treated as quotation marks here.
    """
    prose: List[str] = []
    fenced = False
    for line in spec.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced or re.match(r"^\s*>", line):
            continue
        # Double- and single-quoted spans commonly contain copied log output.
        # Only remove spans that contain a slash, avoiding contractions.
        line = re.sub(r'"[^"\n]*/[^"\n]*"', "", line)
        line = re.sub(r"'[^'\n]*/[^'\n]*'", "", line)
        prose.append(line)
    return "\n".join(prose)


_NON_TOUCH_CONTEXT_RE = re.compile(
    r"(?:\b(?:do|does|did|must|should)\s+not\s+touch\b|\b(?:out\s+of\s+scope|owned\s+by|handled\s+by|belongs\s+to)\b|\bsibling\s+task\b)",
    re.IGNORECASE,
)


def _spec_overlap_paths(spec: str) -> List[str]:
    """Return cited paths that represent this task's potential file access.

    A path explicitly marked as another task's responsibility or as forbidden
    to touch remains subject to the missing-path guard, but is not evidence of
    a same-wave write/read overlap.
    """
    paths = _spec_referenced_paths(spec)
    excluded: Set[str] = set()
    for line in spec.splitlines():
        if not _NON_TOUCH_CONTEXT_RE.search(line):
            continue
        for path in paths:
            if path in line:
                excluded.add(path)
    return [path for path in paths if path not in excluded]


def workspace_roots(project_root: str) -> List[Path]:
    """Return the repository root and explicitly declared workspace roots.

    Workspace members come from the repository's ``uv``, npm/yarn, or pnpm
    workspace metadata. We do not search arbitrary descendants: resolving a
    package-relative path from an unrelated package would be worse than
    reporting it missing.
    """
    root = Path(project_root)
    roots = [root]

    def add_patterns(patterns, include_source_roots: bool = False) -> None:
        if not isinstance(patterns, list):
            return
        for pattern in patterns:
            if not isinstance(pattern, str):
                continue
            for member in root.glob(pattern):
                if member.is_dir() and member not in roots:
                    roots.append(member)
                    # JS/TS packages commonly describe files relative to their
                    # source directory, even when the workspace member is the
                    # package directory itself.
                    source_root = member / "src"
                    if include_source_roots and source_root.is_dir() and source_root not in roots:
                        roots.append(source_root)

    try:
        with (root / "pyproject.toml").open("rb") as handle:
            metadata = tomllib.load(handle)
        add_patterns(metadata.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", []))
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        pass

    try:
        with (root / "package.json").open(encoding="utf-8") as handle:
            workspaces = json.load(handle).get("workspaces", [])
        if isinstance(workspaces, dict):
            workspaces = workspaces.get("packages", [])
        add_patterns(workspaces, include_source_roots=True)
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass

    try:
        with (root / "pnpm-workspace.yaml").open(encoding="utf-8") as handle:
            add_patterns((yaml.safe_load(handle) or {}).get("packages", []), include_source_roots=True)
    except (OSError, TypeError, AttributeError, yaml.YAMLError):
        pass
    return roots


def planned_spec_paths(spec: str) -> List[str]:
    """Return cited paths this spec explicitly says it will create."""
    paths: List[str] = []
    for line in spec.splitlines():
        lowered = line.lower()
        if not re.search(r"\b(new file|create|created|will create|add .*file)\b", lowered):
            continue
        for path in _spec_referenced_paths(line):
            if path not in paths:
                paths.append(path)
    return paths


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
    roots = workspace_roots(project_root)
    if worktree:
        project_path = Path(project_root).resolve()
        roots = [Path(worktree) / root.resolve().relative_to(project_path) for root in roots]
    missing = []
    for rel in _spec_referenced_paths(spec):
        matches = [root / rel for root in roots if (root / rel).exists()]
        if len(matches) != 1:
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
        from snodo.tools.git import open_repo
        with open_repo(project_root) as repo:
            return sorted(repo.untracked_files)
    except Exception:  # noqa: BLE001 — best-effort; never block on this
        return []


def merge_lock(project_root: str):
    """Return a re-entrant process/thread-safe file lock for repository merges.

    Serialises all operations that read or modify the base repository's git ref
    and index state (gate check, merge, commit SHA resolution, ref updates,
    worktree creation/teardown, branch cleanup).
    """
    from filelock import FileLock

    lock_path = Path(project_root) / ".snodo" / ".merge.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(lock_path), is_singleton=True)


def stale_index_lock(project_root: str, error: Exception) -> bool:
    """Return whether an index lock error names an unheld lock file.

    ``lsof`` is used only as an observer. A missing command or an inspection
    failure means the lock cannot be diagnosed here, not that it is stale.
    """
    if "index.lock" not in str(error):
        return False

    lock_path = Path(project_root) / ".git" / "index.lock"
    if not lock_path.exists():
        return False

    try:
        result = subprocess.run(  # noqa: S603 - fixed argv; lock path is one argument
            ["lsof", "-t", "--", str(lock_path)],  # noqa: S607 - resolved from PATH by design
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _logger.debug("Could not inspect Git index lock %s", lock_path, exc_info=True)
        return False

    return result.returncode == 1 and not result.stdout.strip()


def create_worktree(
    project_root: str,
    task_id: str,
    spec: str,
    branch: Optional[str] = None,
    base: Optional[str] = None,
    plan_name: Optional[str] = None,
) -> Path:
    """Create a git worktree for *task_id*.

    Creates a branch off the resolved base branch at the worktree path. Existing
    task work is reused on retry instead of being destroyed. Operators who want
    to discard it can explicitly run ``snodo worktree remove <task_id>`` first.

    Returns:
        Absolute path to the new worktree.
    """
    from snodo.tools.git import open_repo, resolve_base_branch

    wt_path, resolved_branch = _task_identity(project_root, task_id, spec, plan_name)
    branch_name = branch or resolved_branch
    base_branch = base or resolve_base_branch(project_root)
    # Retrying must not erase work from a halted attempt. This also covers the
    # legacy plan identity; the selected path is the task's durable workspace.
    if wt_path.exists():
        _logger.info("Reusing existing worktree %s for task %s", wt_path, task_id)
        return wt_path

    with merge_lock(project_root):
        with open_repo(project_root) as repo:
            # A repository with no commits has an unborn HEAD: the base branch
            # does not resolve, so `git worktree add` fails with "invalid
            # reference". This is the state every greenfield repository starts
            # in — the agent would otherwise run in the operator's real working
            # tree. Refuse loudly with actionable guidance rather than degrading
            # isolation (Fixes #29). Callers that accept a degraded run must say
            # so explicitly.
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

            branch_exists = branch_name in repo.heads

            if branch_exists:
                # Keep a branch from an earlier attempt intact. Attaching it to
                # the expected path carries committed work into the retry.
                repo.git.worktree("add", str(wt_path), branch_name)
            else:
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
    plan_name: Optional[str] = None,
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
    return str(create_worktree(project_root, task_id, spec, plan_name=plan_name))


def _remove_worktree_metadata(repo, wt_path: Path) -> None:
    """Remove only Git's admin entry for *wt_path*.

    ``git worktree prune`` has no path filter and could remove unrelated stale
    worktrees. Each linked worktree instead records its own checkout metadata
    in ``common_dir/worktrees/<name>/gitdir``, so match that entry directly.
    """
    target_gitdir = (wt_path / ".git").resolve()
    metadata_dir = Path(repo.common_dir).resolve() / "worktrees"
    if not metadata_dir.is_dir():
        return

    for entry in metadata_dir.iterdir():
        gitdir_file = entry / "gitdir"
        if not gitdir_file.is_file():
            continue
        try:
            gitdir = Path(gitdir_file.read_text().strip())
            if not gitdir.is_absolute():
                gitdir = entry / gitdir
            if gitdir.resolve() == target_gitdir:
                shutil.rmtree(entry, ignore_errors=True)
                return
        except OSError:
            continue


def remove_worktree(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> None:
    """Remove the worktree for *task_id* (force, best-effort)."""
    wt_path, _ = _task_identity(project_root, task_id, "", plan_name)
    if not worktree_is_owned(project_root, task_id, plan_name):
        _logger.warning("Worktree %s is not owned by project %s", wt_path, project_root)
        return
    if not wt_path.exists():
        return
    with merge_lock(project_root):
        try:
            from git import GitCommandError
            from snodo.tools.git import open_repo
            with open_repo(project_root) as repo:
                try:
                    repo.git.worktree("remove", "--force", str(wt_path))
                except GitCommandError:
                    shutil.rmtree(str(wt_path), ignore_errors=True)
                    _remove_worktree_metadata(repo, wt_path)
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
        from snodo.tools.git import open_repo
        with open_repo(project_root) as repo:
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

    with merge_lock(project_root):
        git = GitMCP(project_root)
        try:
            git.merge_branch(branch)
            return "merged", []
        except MergeConflictError as e:
            return "conflict", getattr(e, "conflicting_paths", [])


def delete_task_branch(project_root: str, branch: str) -> None:
    """Delete the task branch (best-effort, after a successful merge)."""
    with merge_lock(project_root):
        try:
            from git import GitCommandError
            from snodo.tools.git import open_repo
            with open_repo(project_root) as repo:
                try:
                    repo.git.branch("-D", branch)
                except GitCommandError as e:
                    _logger.debug("Failed to delete branch %s: %s", branch, e)
        except Exception as e:
            _logger.debug("Failed to delete task branch %s: %s", branch, e)


def _delete_branch(repo, name: str) -> bool:
    """Delete one branch via git; return True when it was removed."""
    from git import GitCommandError
    try:
        repo.git.branch("-D", name)
        return True
    except GitCommandError as e:
        _logger.debug("Failed to delete branch %s: %s", name, e)
        return False


def delete_task_branches(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> List[str]:
    """Delete every branch for *task_id*.

    The worktree must already be removed: git refuses to delete a branch that
    is checked out in a worktree. This is the discard path (``snodo task
    abandon``/``prune``), where the operator has explicitly given up on the
    work — unlike the success-path teardown, it deletes unmerged branches too.
    Returns the branch names deleted.
    """
    deleted: List[str] = []
    with merge_lock(project_root):
        try:
            from snodo.tools.git import open_repo
            with open_repo(project_root) as repo:
                prefixes = [f"task/{task_id}"]
                if plan_name:
                    prefixes.insert(0, f"task/{_name_component(plan_name)}/{task_id}")
                for name in [
                    head.name for head in repo.heads
                    if any(head.name == prefix or head.name.startswith(f"{prefix}/") for prefix in prefixes)
                ]:
                    if _delete_branch(repo, name):
                        deleted.append(name)
        except Exception as e:
            _logger.debug("Could not delete task branches for %s: %s", task_id, e)
    return deleted


def delete_merged_task_branches(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> List[str]:
    """Delete branches for *task_id* whose work is already contained in the base.

    A branch that is not in the base holds work the operator has not merged;
    deleting it would discard that work, so it is left alone. This is the
    success-path rule: a clean merge is the only reason a completed task's
    branch may be removed. The worktree must be gone first. Returns the branch
    names deleted.
    """
    deleted: List[str] = []
    with merge_lock(project_root):
        try:
            from snodo.tools.git import open_repo, resolve_base_branch
            base = resolve_base_branch(project_root)
            with open_repo(project_root) as repo:
                try:
                    raw = repo.git.branch("--merged", base)
                    merged = {
                        line.strip().lstrip("*+ ").strip()
                        for line in raw.splitlines()
                        if line.strip().lstrip("*+ ").strip()
                    } if isinstance(raw, str) else set()
                except Exception as e:
                    _logger.debug("Could not read merged branches: %s", e)
                    merged = set()
                prefixes = [f"task/{task_id}"]
                if plan_name:
                    prefixes.insert(0, f"task/{_name_component(plan_name)}/{task_id}")
                for head in list(repo.heads):
                    if not any(
                        head.name == prefix or head.name.startswith(f"{prefix}/")
                        for prefix in prefixes
                    ):
                        continue
                    if head.name not in merged:
                        continue
                    if _delete_branch(repo, head.name):
                        deleted.append(head.name)
        except Exception as e:
            _logger.debug("Could not delete merged branches for %s: %s", task_id, e)
    return deleted


def task_branch_is_merged(
    project_root: str,
    task_id: str,
    spec: str,
    plan_name: Optional[str] = None,
) -> Optional[bool]:
    """Whether *task_id*'s branch is already contained in the base branch.

    Ground truth is the repository — the branch's tip and its relationship to
    the resolved base branch — not the plan's status record, which is exactly
    the thing that can be stale (an operator may merge the branch by hand and
    leave ``unmerged`` behind). Returns:

    * ``True``  — the branch exists and its tip is an ancestor of the base tip.
    * ``False`` — the branch exists and its tip is not contained in the base.
    * ``None``  — the branch does not exist, or git cannot answer. Callers
      keep their existing behaviour rather than treating an unreadable
      repository as a merge.

    "No branch" is deliberately not folded into "not merged": a task with no
    branch at all is unaffected by this check.
    """
    from snodo.tools.git import open_repo, resolve_base_branch

    _, branch = _task_identity(project_root, task_id, spec, plan_name)
    try:
        base = resolve_base_branch(project_root)
        with open_repo(project_root) as repo:
            if branch not in repo.heads:
                return None
            try:
                base_commit = repo.commit(base)
            except Exception as e:
                _logger.debug("Could not resolve base commit for %s: %s", base, e)
                return None
            if hasattr(repo, "is_ancestor"):
                try:
                    ancestor = repo.is_ancestor(repo.commit(branch), base_commit)
                    if isinstance(ancestor, bool):
                        return ancestor
                except Exception as e:
                    _logger.debug("Could not check ancestry of %s: %s", branch, e)
            try:
                raw = repo.git.branch("--merged", base)
            except Exception as e:
                _logger.debug("Could not read merged branches for %s: %s", branch, e)
                return None
            merged = {
                line.strip().lstrip("*+ ").strip()
                for line in raw.splitlines()
                if line.strip().lstrip("*+ ").strip()
            } if isinstance(raw, str) else set()
            return branch in merged
    except Exception as e:
        _logger.debug("Could not determine whether %s is merged: %s", branch, e)
        return None


def teardown_task_worktree(
    project_root: str, task_id: str, plan_name: Optional[str] = None
) -> None:
    """Tear down a task's isolation: worktree first, then its merged branches.

    The single home for this sequence. A branch checked out in a worktree
    cannot be deleted until that worktree is removed, so the order is
    load-bearing and must not be re-implemented elsewhere. Only branches whose
    work is already in the base are deleted: a completed run that did not merge
    is not silently discarded.

    Both the CLI inline path and the background job wrapper route through this
    helper, so the two cannot disagree about the identity or the order.
    """
    remove_worktree(project_root, task_id, plan_name)
    delete_merged_task_branches(project_root, task_id, plan_name)


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
    owned = _project_worktree_paths(project_root)
    entries = [
        p for p in d.rglob("*")
        if p.is_dir() and not p.name.startswith(".") and p.resolve() in owned
    ]
    entries.sort(key=lambda p: p.stat().st_mtime)
    return [p.name for p in entries]


def list_task_branches(project_root: str) -> Tuple[bool, dict]:
    """Return (git_available, task_branches) for all task/* branches in project_root.

    task_branches maps branch task_id/prefix to {name, commit_date, is_merged}.
    Containment in the resolved base branch is evaluated as ground truth via git.
    """
    from datetime import datetime, timezone
    from snodo.tools.git import GitMCP, resolve_base_branch

    git_task_branches: dict = {}
    try:
        git = GitMCP(project_root)
        base_branch = resolve_base_branch(project_root)
        git_merged_branches = set()
        try:
            raw = git.repo.git.branch("--merged", base_branch)
            if isinstance(raw, str):
                git_merged_branches = {
                    line.strip().lstrip("*+ ").strip()
                    for line in raw.splitlines()
                    if line.strip().lstrip("*+ ").strip()
                }
        except Exception as e:
            _logger.debug("Could not read merged branches: %s", e)

        base_commit = None
        try:
            base_commit = git.repo.commit(base_branch)
        except Exception as e:
            _logger.debug("Could not resolve base commit for %s: %s", base_branch, e)

        for head in git.repo.heads:
            if head.name.startswith("task/"):
                branch_suffix = head.name[5:]
                components = branch_suffix.split("/")
                # New branches are task/<plan>/<task_id>/<slug>; retain both
                # ownership components as the map key so equal task ids from
                # different plans cannot collapse into one listing row.
                task_id = (
                    "/".join(components[:2])
                    if len(components) >= 3
                    else components[0]
                )
                try:
                    commit_ts = datetime.fromtimestamp(head.commit.committed_date, tz=timezone.utc)
                except Exception:
                    commit_ts = None

                is_contained = head.name in git_merged_branches
                if not is_contained and base_commit is not None and hasattr(git.repo, "is_ancestor"):
                    try:
                        anc = git.repo.is_ancestor(head.commit, base_commit)
                        if isinstance(anc, bool) and anc is True:
                            is_contained = True
                    except Exception as e:
                        _logger.debug("Could not check is_ancestor for %s: %s", head.name, e)

                branch_info = {
                    "name": head.name,
                    "commit_date": commit_ts,
                    "is_merged": is_contained,
                }
                if task_id in git_task_branches:
                    existing = git_task_branches[task_id]
                    if existing.get("is_merged") and not is_contained:
                        git_task_branches[task_id] = branch_info
                    elif not existing.get("is_merged") and is_contained:
                        pass
                    elif commit_ts and (not existing.get("commit_date") or commit_ts > existing["commit_date"]):
                        git_task_branches[task_id] = branch_info
                else:
                    git_task_branches[task_id] = branch_info
        return True, git_task_branches
    except Exception as e:
        _logger.warning("Could not inspect git task branches: %s", e)
        return False, {}


def task_branch_has_no_changes(
    project_root: str,
    task_id: str,
    spec: str = "",
    plan_name: Optional[str] = None,
) -> bool:
    """True when the task branch HEAD matches the base branch commit (no diff produced)."""
    try:
        from snodo.tools.git import open_repo, resolve_base_branch
        _, branch = _task_identity(project_root, task_id, spec, plan_name)
        with open_repo(str(Path(project_root))) as repo:
            base = resolve_base_branch(project_root)
            return bool(
                branch in repo.heads
                and base in repo.heads
                and repo.heads[branch].commit == repo.heads[base].commit
            )
    except Exception:
        return False
