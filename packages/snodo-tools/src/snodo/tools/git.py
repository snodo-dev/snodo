"""Git MCP server for git operations.

FILE: snodo/mcp/git.py

Implements git operations for reviewer mode transitions.
Enforces capability boundaries by validating paths and ensuring
operations stay within the project root.

Uses GitPython for all git operations (no subprocess calls).
"""

import os
from pathlib import Path
from typing import List, Optional

from git import Repo, GitCommandError, InvalidGitRepositoryError

from snodo.tools.workspace import PathValidationError


class GitError(Exception):
    """Raised when a git operation fails."""


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
            self.repo = Repo(str(self.project_root), search_parent_directories=True)
        except InvalidGitRepositoryError:
            raise ValueError(f"Not a git repository: {self.project_root}")

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
        except ValueError:
            raise PathValidationError(
                f"Path escapes project root: {path} -> {resolved}"
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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
                raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

        try:
            return self.repo.git.merge(branch)
        except GitCommandError as e:
            stderr = e.stderr.strip() if e.stderr else str(e)
            if _has_merge_conflict(self.repo):
                conflicting_paths = []
                try:
                    unmerged = self.repo.index.unmerged_blobs()
                    conflicting_paths = sorted(list(unmerged.keys()))
                except Exception:
                    pass
                try:
                    self.repo.git.merge("--abort")
                except GitCommandError:
                    pass
                paths_str = ", ".join(conflicting_paths) if conflicting_paths else "unknown path(s)"
                raise MergeConflictError(
                    f"Merge conflict merging '{branch}' into '{base}' in [{paths_str}]: {stderr}",
                    conflicting_paths=conflicting_paths,
                ) from e

            if "overwritten by merge" in stderr or "overwritten by checkout" in stderr:
                staged_files = []
                try:
                    staged_files = self.repo.git.diff("--cached", "--name-only").splitlines()
                except Exception:
                    pass
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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

    def read_diff(self) -> str:
        """Read current working tree diff.

        Shows both staged and unstaged changes against HEAD.

        Returns:
            Diff output as string
        """
        try:
            return self.repo.git.diff("HEAD")
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

    def get_status(self) -> str:
        """Get git status.

        Returns:
            Status output as string
        """
        try:
            return self.repo.git.status()
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

    def get_head_sha(self) -> str:
        """Read the current HEAD commit sha.

        Returns:
            Full 40-char hex sha of the current HEAD commit
        """
        try:
            return self.repo.head.commit.hexsha
        except Exception as e:
            raise GitError(f"Could not read HEAD sha: {e}")

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
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")


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
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
    except InvalidGitRepositoryError:
        return "main"

    # Remote default branch (e.g. origin/HEAD -> refs/remotes/origin/main).
    try:
        remote_head = repo.git.symbolic_ref("refs/remotes/origin/HEAD")
        return remote_head.split("/")[-1]
    except GitCommandError:
        pass

    return "main"


def _has_merge_conflict(repo) -> bool:
    """Return True if the repository has unmerged (conflicted) paths."""
    try:
        return bool(repo.index.unmerged_blobs())
    except Exception:
        return False
