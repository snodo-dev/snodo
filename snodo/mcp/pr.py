"""PR MCP server for pull request operations.

FILE: snodo/mcp/pr.py

Implements PR operations by delegating to a CodeHostProvider.
The provider abstracts the code host platform (GitHub, local, plugins).
"""

from pathlib import Path
from typing import Optional

from snodo.providers.base import CodeHostProvider, ProviderError


class PrError(Exception):
    """Raised when a PR operation fails."""


class PrMCP:
    """MCP server for PR operations.

    Delegates all operations to a CodeHostProvider instance.
    The provider is set during initialization (by ProtocolMCPServer)
    or can be auto-detected.
    """

    def __init__(
        self,
        project_root: str,
        provider: Optional[CodeHostProvider] = None,
    ):
        """Initialize PR MCP.

        Args:
            project_root: Absolute path to project root directory
            provider: Code host provider. If None, operations will fail
                with a helpful error until a provider is set.
        """
        self.project_root = Path(project_root).resolve()

        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")

        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")

        self._provider = provider

    @property
    def provider(self) -> CodeHostProvider:
        """Get the configured provider, raising if none set."""
        if self._provider is None:
            raise PrError(
                "No code host provider configured. "
                "Set provider in protocol.yml metadata or configure a git remote."
            )
        return self._provider

    @provider.setter
    def provider(self, value: CodeHostProvider) -> None:
        self._provider = value

    def create_pr(self, branch: str, title: str, body: str) -> str:
        """Create a pull request."""
        try:
            return self.provider.create_pr(branch, title, body)
        except ProviderError as e:
            raise PrError(str(e))

    def read_pr_diff(self, pr_number: int) -> str:
        """Read the diff of a pull request."""
        try:
            return self.provider.read_pr_diff(pr_number)
        except ProviderError as e:
            raise PrError(str(e))

    def post_review_comment(self, pr_number: int, comment: str) -> str:
        """Post a comment on a pull request."""
        try:
            return self.provider.post_review_comment(pr_number, comment)
        except ProviderError as e:
            raise PrError(str(e))

    def approve_pr(self, pr_number: int) -> str:
        """Approve a pull request."""
        try:
            return self.provider.approve_pr(pr_number)
        except ProviderError as e:
            raise PrError(str(e))

    def reject_pr(self, pr_number: int, reason: str) -> str:
        """Request changes on a pull request."""
        try:
            return self.provider.reject_pr(pr_number, reason)
        except ProviderError as e:
            raise PrError(str(e))

    def merge_pr(self, pr_number: int) -> str:
        """Merge a pull request."""
        try:
            return self.provider.merge_pr(pr_number)
        except ProviderError as e:
            raise PrError(str(e))

    def read_pr_comments(self, pr_number: int) -> str:
        """Read comments and reviews on a pull request."""
        try:
            return self.provider.read_pr_comments(pr_number)
        except ProviderError as e:
            raise PrError(str(e))
