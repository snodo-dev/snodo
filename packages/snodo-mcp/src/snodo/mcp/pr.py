"""MCP handlers for vendor-neutral code-host change requests."""

from pathlib import Path
from typing import Optional

from snodo.providers.base import CodeHostProvider, ProviderError


class PrError(Exception):
    """Raised when a change-request operation fails."""


class PrMCP:
    """Delegate change-request operations to a configured code-host provider."""

    def __init__(self, project_root: str, provider: Optional[CodeHostProvider] = None):
        self.project_root = Path(project_root).resolve()
        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")
        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")
        self._provider = provider

    @property
    def provider(self) -> CodeHostProvider:
        if self._provider is None:
            raise PrError(
                "No code host provider configured. Set provider in protocol.yml "
                "metadata or configure a git remote."
            )
        return self._provider

    @provider.setter
    def provider(self, value: CodeHostProvider) -> None:
        self._provider = value

    def _call(self, method: str, *args) -> str:
        try:
            return getattr(self.provider, method)(*args)
        except ProviderError as e:
            raise PrError(str(e)) from e

    def create_change_request(self, branch: str, title: str, body: str) -> str:
        return self._call("create_change_request", branch, title, body)

    def read_change_request_diff(self, change_request_id: str) -> str:
        return self._call("read_change_request_diff", change_request_id)

    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        return self._call("post_change_request_comment", change_request_id, comment)

    def approve_change_request(self, change_request_id: str) -> str:
        return self._call("approve_change_request", change_request_id)

    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        return self._call("request_change_request_changes", change_request_id, reason)

    def merge_change_request(self, change_request_id: str) -> str:
        return self._call("merge_change_request", change_request_id)

    def read_change_request_discussion(self, change_request_id: str) -> str:
        return self._call("read_change_request_discussion", change_request_id)

    # Deprecated method aliases remain available for existing callers.
    def create_pr(self, branch: str, title: str, body: str) -> str:
        return self.create_change_request(branch, title, body)

    def read_pr_diff(self, pr_number: int) -> str:
        return self.read_change_request_diff(str(pr_number))

    def post_review_comment(self, pr_number: int, comment: str) -> str:
        return self.post_change_request_comment(str(pr_number), comment)

    def approve_pr(self, pr_number: int) -> str:
        return self.approve_change_request(str(pr_number))

    def reject_pr(self, pr_number: int, reason: str) -> str:
        return self.request_change_request_changes(str(pr_number), reason)

    def merge_pr(self, pr_number: int) -> str:
        return self.merge_change_request(str(pr_number))

    def read_pr_comments(self, pr_number: int) -> str:
        return self.read_change_request_discussion(str(pr_number))
