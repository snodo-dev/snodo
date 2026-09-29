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

    def create_change_request(self, branch: str, title: str, body: str, target_branch=None) -> str:
        raise ProviderError(
            "Cannot create PR: no remote code host configured. "
            "Push to a remote and configure a provider."
        )

    def read_change_request_diff(self, change_request_id: str) -> str:
        raise ProviderError(
            f"Cannot read change request {change_request_id}: no remote code host configured."
        )

    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        raise ProviderError(
            "Cannot post comment: no remote code host configured."
        )

    def approve_change_request(self, change_request_id: str) -> str:
        raise ProviderError(
            "Cannot approve PR: no remote code host configured."
        )

    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        raise ProviderError(
            "Cannot reject PR: no remote code host configured."
        )

    def merge_change_request(self, change_request_id: str) -> str:
        raise ProviderError(
            "Cannot merge PR: no remote code host configured."
        )

    def read_change_request_discussion(self, change_request_id: str) -> str:
        raise ProviderError(
            f"Cannot read change request {change_request_id} discussion: no remote code host configured."
        )
