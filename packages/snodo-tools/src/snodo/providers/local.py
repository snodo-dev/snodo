"""Local (no-op) code host provider.

FILE: snodo/providers/local.py

Provider for repositories without a remote code host.
PR operations return stub responses. Useful for solo/offline workflows.
"""


from snodo.providers.base import CodeHostProvider, ProviderError


class LocalProvider(CodeHostProvider):
    """No-op provider for local-only repositories.

    All mutating operations return stub responses.
    Read operations return empty/placeholder data.
    """

    def create_pr(self, branch: str, title: str, body: str) -> str:
        raise ProviderError(
            "Cannot create PR: no remote code host configured. "
            "Push to a remote and configure a provider."
        )

    def read_pr_diff(self, pr_number: int) -> str:
        raise ProviderError(
            f"Cannot read PR #{pr_number}: no remote code host configured."
        )

    def post_review_comment(self, pr_number: int, comment: str) -> str:
        raise ProviderError(
            "Cannot post comment: no remote code host configured."
        )

    def approve_pr(self, pr_number: int) -> str:
        raise ProviderError(
            "Cannot approve PR: no remote code host configured."
        )

    def reject_pr(self, pr_number: int, reason: str) -> str:
        raise ProviderError(
            "Cannot reject PR: no remote code host configured."
        )

    def merge_pr(self, pr_number: int) -> str:
        raise ProviderError(
            "Cannot merge PR: no remote code host configured."
        )

    def read_pr_comments(self, pr_number: int) -> str:
        raise ProviderError(
            f"Cannot read PR #{pr_number} comments: no remote code host configured."
        )
