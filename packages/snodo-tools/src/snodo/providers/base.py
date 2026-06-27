"""Code host provider abstract base class.

FILE: snodo/providers/base.py

Defines the interface that all code host providers must implement.
Providers decouple PR operations from any specific platform (GitHub, GitLab, etc).
"""

from abc import ABC, abstractmethod


class ProviderError(Exception):
    """Raised when a provider operation fails."""


class CodeHostProvider(ABC):
    """Abstract base class for code host providers.

    Each provider implements PR operations for a specific platform.
    PrMCP delegates to a concrete provider instance.
    """

    @abstractmethod
    def create_pr(self, branch: str, title: str, body: str) -> str:
        """Create a pull request.

        Args:
            branch: Source branch name
            title: PR title
            body: PR description body

        Returns:
            PR URL or identifier string
        """

    @abstractmethod
    def read_pr_diff(self, pr_number: int) -> str:
        """Read the diff of a pull request.

        Args:
            pr_number: PR number

        Returns:
            Diff output as string
        """

    @abstractmethod
    def post_review_comment(self, pr_number: int, comment: str) -> str:
        """Post a comment on a pull request.

        Args:
            pr_number: PR number
            comment: Comment text

        Returns:
            Confirmation string
        """

    @abstractmethod
    def approve_pr(self, pr_number: int) -> str:
        """Approve a pull request.

        Args:
            pr_number: PR number

        Returns:
            Confirmation string
        """

    @abstractmethod
    def reject_pr(self, pr_number: int, reason: str) -> str:
        """Request changes on a pull request.

        Args:
            pr_number: PR number
            reason: Reason for rejection

        Returns:
            Confirmation string
        """

    @abstractmethod
    def merge_pr(self, pr_number: int) -> str:
        """Merge a pull request.

        Args:
            pr_number: PR number

        Returns:
            Confirmation string
        """

    @abstractmethod
    def read_pr_comments(self, pr_number: int) -> str:
        """Read comments and reviews on a pull request.

        Returns JSON string with keys: title, comments, reviews.
        Each comment has: author.login, body
        Each review has: author.login, body, state

        Args:
            pr_number: PR number

        Returns:
            JSON string
        """
