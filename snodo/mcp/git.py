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


class GitError(Exception):
    """Raised when a git operation fails."""


class PathValidationError(Exception):
    """Raised when path validation fails."""


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
            self.repo = Repo(str(self.project_root))
        except InvalidGitRepositoryError:
            raise ValueError(f"Not a git repository: {self.project_root}")

    def validate_path(self, path: str) -> Path:
        """Validate that path is within project root.

        Args:
            path: Path to validate (relative or absolute)

        Returns:
            Resolved absolute Path object

        Raises:
            PathValidationError: If path escapes project root
        """
        if os.path.isabs(path):
            resolved = Path(path).resolve()
        else:
            resolved = (self.project_root / path).resolve()

        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PathValidationError(
                f"Path escapes project root: {path} -> {resolved}"
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

    def stage_files(self, paths: List[str]) -> str:
        """Stage files for commit.

        Args:
            paths: List of file paths to stage

        Returns:
            Command output

        Raises:
            PathValidationError: If any path escapes project root
        """
        validated_paths = []
        for path in paths:
            validated = self.validate_path(path)
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

    def merge_branch(self, branch: str) -> str:
        """Merge a branch into main.

        Checks out main and merges the specified branch.

        Args:
            branch: Name of the branch to merge

        Returns:
            Command output
        """
        try:
            self.repo.git.checkout("main")
            return self.repo.git.merge(branch)
        except GitCommandError as e:
            raise GitError(f"Git command failed: {e.stderr.strip() if e.stderr else str(e)}")

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
