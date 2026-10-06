"""Vendor-neutral code-host provider contract."""

from abc import ABC, abstractmethod


CODE_HOST_PROVIDER_INTERFACE_VERSION = 1


class ProviderError(Exception):
    """Raised when a provider operation fails."""


class CodeHostProvider(ABC):
    """Interface for change requests and their discussions."""

    @classmethod
    def claims_remote(cls, url: str) -> bool:
        """Return whether this provider recognizes a Git remote URL."""
        return False

    def resolve_change_request_refs(self, change_request_id: str) -> tuple[str, str]:
        """Return (base ref, head ref) for a hosted change request."""
        raise ProviderError("This provider cannot resolve change-request refs")

    def __init_subclass__(cls, **kwargs):
        """Adapt legacy provider subclasses while they migrate to the new API."""
        super().__init_subclass__(**kwargs)
        legacy = {
            "create_change_request": "create_pr",
            "read_change_request_diff": "read_pr_diff",
            "post_change_request_comment": "post_review_comment",
            "approve_change_request": "approve_pr",
            "request_change_request_changes": "reject_pr",
            "merge_change_request": "merge_pr",
            "read_change_request_discussion": "read_pr_comments",
        }
        if all(name in cls.__dict__ for name in legacy.values()):
            for new_name, old_name in legacy.items():
                if new_name not in cls.__dict__:
                    old_method = cls.__dict__[old_name]
                    setattr(cls, new_name, lambda self, *args, _m=old_method, **kw: _m(self, *args, **kw))

    @abstractmethod
    def create_change_request(
        self, branch: str, title: str, body: str, target_branch: str | None = None
    ) -> str:
        """Create a change request from ``branch`` into ``target_branch``."""

    @abstractmethod
    def read_change_request_diff(self, change_request_id: str) -> str:
        """Read a change request's diff using its opaque identifier."""

    @abstractmethod
    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        """Post a discussion comment."""

    @abstractmethod
    def approve_change_request(self, change_request_id: str) -> str:
        """Approve a change request."""

    @abstractmethod
    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        """Request changes on a change request."""

    @abstractmethod
    def merge_change_request(self, change_request_id: str) -> str:
        """Merge a change request."""

    @abstractmethod
    def read_change_request_discussion(self, change_request_id: str) -> str:
        """Return JSON with title and comments/reviews using neutral author strings."""

    # Compatibility for existing integrations. These wrappers intentionally accept
    # integer identifiers and delegate to the new opaque-string interface.
    def create_pr(self, branch: str, title: str, body: str) -> str:
        """Deprecated compatibility alias for create_change_request."""
        return self.create_change_request(branch, title, body)

    def read_pr_diff(self, pr_number: int) -> str:
        """Deprecated compatibility alias for read_change_request_diff."""
        return self.read_change_request_diff(str(pr_number))

    def post_review_comment(self, pr_number: int, comment: str) -> str:
        """Deprecated compatibility alias for post_change_request_comment."""
        return self.post_change_request_comment(str(pr_number), comment)

    def approve_pr(self, pr_number: int) -> str:
        """Deprecated compatibility alias for approve_change_request."""
        return self.approve_change_request(str(pr_number))

    def reject_pr(self, pr_number: int, reason: str) -> str:
        """Deprecated compatibility alias for request_change_request_changes."""
        return self.request_change_request_changes(str(pr_number), reason)

    def merge_pr(self, pr_number: int) -> str:
        """Deprecated compatibility alias for merge_change_request."""
        return self.merge_change_request(str(pr_number))

    def read_pr_comments(self, pr_number: int) -> str:
        """Deprecated compatibility alias for read_change_request_discussion."""
        return self.read_change_request_discussion(str(pr_number))
