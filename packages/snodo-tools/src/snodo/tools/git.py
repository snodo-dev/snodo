"""Git MCP server for git operations.

FILE: snodo/mcp/git.py

Implements git operations for reviewer mode transitions.
Enforces capability boundaries by validating paths and ensuring
operations stay within the project root.

Uses GitPython for all git operations (no subprocess calls).
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from git import Repo, GitCommandError, InvalidGitRepositoryError
from git.db import GitDB

from snodo.tools.workspace import PathValidationError

_logger = logging.getLogger(__name__)


def open_repo(
    project_root: str,
    *,
    search_parent_directories: bool = True,
) -> Repo:
    """Open a repository without spawning a persistent git child.

    GitPython's default object DB (:class:`git.db.GitCmdObjectDB`) lazily
    starts a long-lived ``git cat-file --batch-check`` subprocess the first
    time an object is read, and ends it only from ``Repo.__del__``. Because
    GitPython's ``Repo``/``Git`` graph contains reference cycles, that
    finalizer runs on a later cyclic-GC pass — an unbounded time after the
    work that needed the object, and possibly in an unrelated test or task.
    The child dies by ``Popen.terminate()``, i.e. ``os.kill(pid, SIGTERM)``,
    which is how a test suite came to signal a process it never created.

    Opening with the pure-Python :class:`git.db.GitDB` reads objects in this
    process, so no persistent child exists to be reaped later. Every code path
    that merely needs a ref, a diff, or a worktree operation gets the same
    answers; the only cost is that large object reads are not pipelined
    through a helper process.
    """
    return Repo(
        str(Path(project_root)),
        odbt=GitDB,
        search_parent_directories=search_parent_directories,
    )


class GitError(Exception):
    """Raised when a git operation fails."""


#: How many changed files :meth:`GitMCP.change_size` will count lines for.
#: Counting files is a tree walk; counting lines compares the content of
#: every changed file. A plan run must never stall on a statistic nobody is
#: waiting for, so past this bound the line totals are not computed at all
#: and the record says so (``capped``), rather than a wide diff being
#: presented as a fast one (Fixes #377).
CHANGE_SIZE_MAX_FILES = 500


def _split_nul_fields(raw: str) -> List[str]:
    """Split NUL-terminated git output into fields, dropping the empty tail."""
    return [f for f in raw.split("\0") if f != ""]


def _name_status_records(raw: str) -> List[str]:
    """The status letter of each ``--name-status -z`` record, in order.

    Under ``-z`` every field is its own NUL-terminated string: ``M\\0path``
    or ``R100\\0from\\0to``. A record's field count follows its letter, so
    the walk advances by that, never by guessing which field is next.
    """
    fields = _split_nul_fields(raw)
    letters: List[str] = []
    i = 0
    while i < len(fields):
        letter = fields[i][:1] if fields[i] else "?"
        letters.append(letter)
        i += 3 if letter in ("R", "C") else 2
    return letters


def _numstat_records(raw: str) -> List[tuple]:
    """``(added, deleted)`` counts per ``--numstat -z`` record, in order.

    A record is ``added<TAB>deleted<TAB>path\\0``; a rename carries an
    empty path and its two sides as the following fields. ``-`` — git's
    own marker for a file whose lines cannot be counted — stays ``None``
    and is never folded into ``0``.
    """
    fields = _split_nul_fields(raw)
    records: List[tuple] = []
    i = 0
    while i < len(fields):
        try:
            raw_add, raw_del, path = fields[i].split("\t", 2)
        except ValueError:
            break
        i += 1
        if path == "":
            # ``R100``-style record: the two paths follow as separate fields.
            i += 2
        added = None if raw_add == "-" else int(raw_add)
        deleted = None if raw_del == "-" else int(raw_del)
        records.append((added, deleted))
    return records


def _count_change_shapes(name_status_raw: str, numstat_raw: str) -> dict:
    """Reduce the two metadata diff outputs to per-shape file and line counts.

    ``--name-status -z`` and ``--numstat -z`` report the same records in
    the same order for the same range, so pairing them is what separates
    the line-neutral shapes from each other: a modified file with no line
    movement is a mode or type change, a zero-line ``A`` is a new empty
    file, and neither may be reported as the other.

    A file whose lines cannot be counted (binary) is reported in
    ``files_binary`` and contributes nothing to the line totals: "no
    lines" and "not countable" must not share a zero. A renamed file's own
    edits are countable lines and land in the totals; a deletion's removed
    lines are counted, honestly, as deletions — never as additions.
    """
    letters = _name_status_records(name_status_raw)
    stats = _numstat_records(numstat_raw)
    counts = {
        "files_changed": 0,
        "files_added": 0,
        "files_deleted": 0,
        "files_renamed": 0,
        "files_mode_only": 0,
        "files_binary": 0,
        "lines_added": 0,
        "lines_deleted": 0,
    }
    for letter, (added, deleted) in zip(letters, stats):
        counts["files_changed"] += 1
        if letter == "A":
            counts["files_added"] += 1
        elif letter == "D":
            counts["files_deleted"] += 1
        elif letter in ("R", "C"):
            counts["files_renamed"] += 1
        if added is None or deleted is None:
            counts["files_binary"] += 1
            continue
        counts["lines_added"] += added
        counts["lines_deleted"] += deleted
        if letter not in ("A", "D", "R", "C") and added == 0 and deleted == 0:
            counts["files_mode_only"] += 1
    return counts


def _change_size_capped(
    base_sha: str, head_sha: str, files_changed: int, paths: List[str], max_files: int
) -> dict:
    """The record for a change too wide to count lines for.

    ``files_changed`` stays real; everything that would have needed the
    content comparison is ``None`` — not measured — with ``capped`` True.
    A null is not a zero: the consumer can tell "no lines" from "no
    counting was done".
    """
    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files_changed": files_changed,
        # ``capped`` tells consumers this bounded prefix is incomplete.
        "paths": paths[:max_files],
        "files_added": None,
        "files_deleted": None,
        "files_renamed": None,
        "files_mode_only": None,
        "files_binary": None,
        "lines_added": None,
        "lines_deleted": None,
        "capped": True,
    }


def _measure_change_size(repo, base_sha: str, head_sha: str, max_files: int) -> dict:
    """Count how much changed between two commits, bounded by *max_files*.

    At most two git operations, neither of which produces patch text: a
    name-only tree comparison bounds the work before any content is
    compared, and one ``--name-status``/``--numstat`` pair supplies the
    counts. Past the bound the line totals are simply not computed — the
    run is never stalled on a statistic nobody is waiting for.
    """
    names = repo.git.diff("--name-only", "-z", base_sha, head_sha, "--")
    paths = _split_nul_fields(names)
    files_changed = len(paths)
    if files_changed > max_files:
        return _change_size_capped(base_sha, head_sha, files_changed, paths, max_files)
    common = ("--find-renames", base_sha, head_sha, "--")
    status_raw = repo.git.diff("--name-status", "-z", *common)
    numstat_raw = repo.git.diff("--numstat", "-z", *common)
    record = {"base_sha": base_sha, "head_sha": head_sha}
    record.update(_count_change_shapes(status_raw, numstat_raw))
    record["paths"] = paths
    record["capped"] = False
    return record


class MergeConflictError(GitError):
    """Raised when a merge conflicts and is left unresolved.

    The merge is aborted so the base branch stays clean; the source branch and
    its worktree are left intact for a human to resolve.
    """
    def __init__(self, message: str, conflicting_paths: Optional[List[str]] = None):
        super().__init__(message)
        self.conflicting_paths = conflicting_paths or []


class GitMCP:
    """MCP server for git operations within project root.

    Enforces capability boundaries (INV2) by:
    - Validating all paths against project root
    - Blocking directory traversal attacks
    - Normalizing paths to prevent bypass attempts
    """

    def __init__(self, project_root: str):
        """Initialize git MCP with project root.

        Args:
            project_root: Absolute path to project root directory
        """
        self.project_root = Path(project_root).resolve()

        # Ensure project root exists
        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")

        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")

        try:
            self.repo = open_repo(str(self.project_root))
        except InvalidGitRepositoryError as e:
            raise ValueError(f"Not a git repository: {self.project_root}") from e

    def validate_path(self, path: str, for_mutation: bool = False) -> Path:
        """Validate that path is within project root.

        Args:
            path: Path to validate (relative or absolute)
            for_mutation: If True, also validate that path is not protected under .snodo/

        Returns:
            Resolved absolute Path object

        Raises:
            PathValidationError: If path escapes project root or attempts to mutate .snodo/
        """
        if os.path.isabs(path):
            resolved = Path(path).resolve()
        else:
            resolved = (self.project_root / path).resolve()

        try:
            rel = resolved.relative_to(self.project_root)
        except ValueError as e:
            raise PathValidationError(
                f"Path escapes project root: {path} -> {resolved}"
            ) from e

        # Version-control internals are the tooling's bookkeeping, not the work
        # a judge reasons about.  Case-insensitive because on a case-insensitive
        # filesystem ``.GIT`` opens the very same directory (Fixes #273).
        if any(part.lower() == ".git" for part in rel.parts):
            raise PathValidationError(
                f"Path is version-control bookkeeping, not the work under "
                f"review, and is not shown: {path} -> {resolved}"
            )

        if for_mutation and rel.parts and rel.parts[0] == ".snodo":
            raise PathValidationError(
                f"Path is protected under .snodo/ and cannot be mutated: {path} -> {resolved}"
            )

        return resolved

    def create_branch(self, name: str) -> str:
        """Create a new git branch.

        Args:
            name: Name of the branch to create

        Returns:
            Command output
        """
        try:
            return self.repo.git.checkout("-b", name)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def checkout_branch(self, name: str) -> str:
        """Checkout an existing git branch.

        Args:
            name: Name of the branch to checkout

        Returns:
            Command output

        Raises:
            GitError: If branch does not exist or checkout fails
        """
        try:
            return self.repo.git.checkout(name)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def stage_files(self, paths: List[str]) -> str:
        """Stage files for commit.

        Args:
            paths: List of file paths to stage

        Returns:
            Command output

        Raises:
            PathValidationError: If any path escapes project root or mutates .snodo/
        """
        validated_paths = []
        for path in paths:
            validated = self.validate_path(path, for_mutation=True)
            validated_paths.append(str(validated))

        if not validated_paths:
            return ""

        try:
            return self.repo.git.add(*validated_paths)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def commit(self, message: str) -> str:
        """Create a commit with message.

        Args:
            message: Commit message

        Returns:
            Command output
        """
        try:
            return self.repo.git.commit("-m", message)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def merge_branch(self, branch: str, base: Optional[str] = None) -> str:
        """Merge a branch into the base branch.

        Resolves the base branch from the repository (remote default, falling
        back to ``main``) unless *base* is given explicitly.

        A merge conflict aborts the merge (leaving the base branch clean) and
        raises :class:`MergeConflictError` so the caller can escalate while the
        source branch and worktree survive.

        Args:
            branch: Name of the branch to merge
            base: Optional base branch to merge into (default: resolved from repo)

        Returns:
            Command output
        """
        base = base or resolve_base_branch(self.project_root)
        try:
            current_branch = self.repo.active_branch.name
        except Exception:
            current_branch = None

        if current_branch != base:
            try:
                self.repo.git.checkout(base)
            except GitCommandError as e:
                raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

        try:
            return self.repo.git.merge(branch)
        except GitCommandError as e:
            stderr = e.stderr.strip() if e.stderr else str(e)
            if _has_merge_conflict(self.repo):
                conflicting_paths = []
                try:
                    unmerged = self.repo.index.unmerged_blobs()
                    conflicting_paths = sorted(list(unmerged.keys()))
                except Exception as err:
                    _logger.debug("Failed to inspect unmerged blobs: %s", err)
                try:
                    self.repo.git.merge("--abort")
                except GitCommandError as err:
                    _logger.debug("Failed to abort merge: %s", err)
                paths_str = ", ".join(conflicting_paths) if conflicting_paths else "unknown path(s)"
                raise MergeConflictError(
                    f"Merge conflict merging '{branch}' into '{base}' in [{paths_str}]: {stderr}",
                    conflicting_paths=conflicting_paths,
                ) from e

            if "overwritten by merge" in stderr or "overwritten by checkout" in stderr:
                staged_files = []
                try:
                    staged_files = self.repo.git.diff("--cached", "--name-only").splitlines()
                except Exception as err:
                    _logger.debug("Failed to inspect staged diff: %s", err)
                if staged_files:
                    staged_str = ", ".join(staged_files)
                    raise GitError(
                        f"Staged changes in index would be overwritten by merge [{staged_str}]: {stderr}"
                    ) from e

                try:
                    part_files = self.repo.git.diff("--name-only", f"{base}..{branch}").splitlines()
                except Exception:
                    part_files = []
                if part_files:
                    part_str = ", ".join(part_files)
                    raise GitError(
                        f"Local changes collide with participating branch file(s) [{part_str}]: {stderr}"
                    ) from e

            raise GitError(f"Git command failed: {stderr}") from e

    def delete_branch(self, branch: str) -> str:
        """Delete a git branch.

        Args:
            branch: Name of the branch to delete

        Returns:
            Command output
        """
        try:
            return self.repo.git.branch("-d", branch)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def read_diff(self) -> str:
        """Read current working tree diff.

        Shows both staged and unstaged changes against HEAD.

        Returns:
            Diff output as string
        """
        try:
            return self.repo.git.diff("HEAD")
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def get_status(self) -> str:
        """Get git status.

        Returns:
            Status output as string
        """
        try:
            return self.repo.git.status()
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def diff_between_refs(self, ref1: str, ref2: str) -> str:
        """Read diff between two git refs.

        Args:
            ref1: First ref (e.g. "HEAD~1")
            ref2: Second ref (e.g. "HEAD")

        Returns:
            Diff output as string
        """
        try:
            return self.repo.git.diff(f"{ref1}..{ref2}")
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def changed_paths_between_refs(self, ref1: str, ref2: str) -> List[str]:
        """Return every path changed between two refs, including rename sides."""
        try:
            changes = self.repo.commit(ref1).diff(self.repo.commit(ref2))
            paths = {
                path
                for change in changes
                for path in (change.a_path, change.b_path)
                if path
            }
            return sorted(paths)
        except Exception as e:
            raise GitError(f"Git path diff failed: {e}") from e

    def change_size(
        self,
        base: str,
        head: str = "HEAD",
        *,
        max_files: int = CHANGE_SIZE_MAX_FILES,
    ) -> dict:
        """Count how much changed between two refs — sizes, never content.

        Returns a record of line totals, per-shape file counts, and repository-
        relative paths over
        ``base..head`` (see :func:`_measure_change_size`). The diff itself
        is never included: the size of the change is the ask, its content
        leaves the machine only by a different decision.

        Raises:
            GitError: if either ref cannot be resolved or the diff fails.
        """
        try:
            base_sha = self.repo.commit(base).hexsha
            head_sha = self.repo.commit(head).hexsha
        except Exception as e:
            raise GitError(f"Could not resolve refs for change size: {e}") from e
        try:
            return _measure_change_size(self.repo, base_sha, head_sha, max_files)
        except GitError:
            raise
        except Exception as e:
            raise GitError(f"Git change-size diff failed: {e}") from e

    def show(self, ref: str, path: str) -> str:
        """Read a file's content at a specific git ref.

        Args:
            ref: Git ref (e.g. "HEAD", "main", "abc1234")
            path: File path relative to project root

        Returns:
            File content at the given ref
        """
        validated = self.validate_path(path)
        rel_path = str(validated.relative_to(self.project_root))
        try:
            return self.repo.git.show(f"{ref}:{rel_path}")
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e

    def get_head_sha(self) -> str:
        """Read the current HEAD commit sha.

        Returns:
            Full 40-char hex sha of the current HEAD commit
        """
        try:
            return self.repo.head.commit.hexsha
        except Exception as e:
            raise GitError(f"Could not read HEAD sha: {e}") from e

    def log(self, n: int = 5) -> str:
        """Read recent commits in oneline format.

        Args:
            n: Number of recent commits to return

        Returns:
            Git log output as string
        """
        try:
            return self.repo.git.log("--oneline", f"-{n}")
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}") from e


# Module-level instance for convenience
_git_instance: Optional[GitMCP] = None


def get_git(project_root: Optional[str] = None) -> GitMCP:
    """Get git MCP instance.

    Args:
        project_root: Project root directory (uses existing instance if None)

    Returns:
        GitMCP instance
    """
    global _git_instance

    if project_root is not None:
        _git_instance = GitMCP(project_root)

    if _git_instance is None:
        raise ValueError("Git MCP not initialized. Call with project_root first.")

    return _git_instance


def resolve_base_branch(project_root: str) -> str:
    """Resolve the repository's base (default) branch.

    Order of resolution:
    1. The remote's default branch (``refs/remotes/origin/HEAD``).
    2. ``main``.

    This is the single source of truth for "which branch do task branches
    diverge from and merge back into" — never assume ``main`` unconditionally.
    """
    try:
        with open_repo(str(Path(project_root))) as repo:
            # Remote default branch (e.g. origin/HEAD -> refs/remotes/origin/main).
            try:
                remote_head = repo.git.symbolic_ref("refs/remotes/origin/HEAD")
                return remote_head.split("/")[-1]
            except GitCommandError:
                pass
    except InvalidGitRepositoryError:
        return "main"

    return "main"


def _has_merge_conflict(repo) -> bool:
    """Return True if the repository has unmerged (conflicted) paths."""
    try:
        return bool(repo.index.unmerged_blobs())
    except Exception:
        return False
